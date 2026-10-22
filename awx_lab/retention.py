"""Retention of lab-owned artifacts; shared runtime caches are left alone."""

from copy import deepcopy
import re
import shutil

from .system import LabError, RUN_ID


HISTORY_LIMIT = 10
ATTEMPT = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}\Z")


def owned_image(state, image):
    if not RUN_ID.fullmatch(state["id"]) or not isinstance(image, str):
        return False
    prefixes = (f"localhost/awx-lab:{state['id']}", f"localhost/awx-lab-ui:{state['id']}")
    return any(image == prefix or (image.startswith(prefix + "-") and
               re.fullmatch(r"[a-z0-9-]+", image[len(prefix) + 1:])) for prefix in prefixes)


def remember_success(state):
    current = {"image": state["image"], "build": deepcopy(state.get("build", {}))}
    last = state.get("last_good")
    if last and last["image"] != current["image"]:
        state["previous_good"] = last
    state["last_good"] = current


def image_candidates(state):
    keep = {state["image"]}
    for deployment in (state, state.get("last_good", {}), state.get("previous_good", {}),
                       state.get("pending_update", {})):
        keep.update(image for image in (deployment.get("image"), deployment.get("build", {}).get("ui_image")) if image)
    tracked = set(state.get("images", []))
    if any(not owned_image(state, image) for image in tracked):
        raise LabError("Refusing retention cleanup: saved images include a tag not owned by this lab.")
    return tracked - keep


def prune_history(path):
    history = path / "history"
    if history.is_symlink():
        raise LabError("Refusing a symlink as report history.")
    if not history.exists():
        return 0
    entries = sorted((p for p in history.iterdir() if ATTEMPT.fullmatch(p.name) and
                      p.is_dir() and not p.is_symlink()), key=lambda p: p.name, reverse=True)
    for entry in entries[HISTORY_LIMIT:]:
        shutil.rmtree(entry)
    return max(0, len(entries) - HISTORY_LIMIT)


def prune_images(lab):
    candidates = image_candidates(lab.state)
    if not candidates:
        return {"removed_images": [], "retained_images": []}
    # A failure here prevents host removal too; retry on a later successful run.
    removable = lab.cluster.prune_images(candidates)
    runtime = lab.state["runtime"]
    present = set(lab.runner.run([runtime, "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
                                 capture=True).stdout.splitlines())
    removed = []
    for image in sorted(removable):
        if image in present:
            # A tag, never an image ID; preserve shared aliases and build parents.
            lab.runner.run([runtime, "image", "rm", "--no-prune", image], capture=True, timeout=120)
        removed.append(image)
        lab.state["images"] = [tag for tag in lab.state.get("images", []) if tag != image]
        lab.save()
    return {"removed_images": removed, "retained_images": sorted(candidates - set(removed))}
