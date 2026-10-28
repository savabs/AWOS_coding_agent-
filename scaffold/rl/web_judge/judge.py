"""
judge.py — WebJudge for Online-Mind2Web, ported from upstream src/methods/webjudge_online_mind2web.py,
src/utils.py and src/run.py (OSU-NLP-Group/Online-Mind2Web @ f0d805ee, MIT; see NOTICE for deviations).

Per episode (upstream WebJudge_Online_Mind2Web_eval + run.auto_eval):
    1. key points:   one call (KEY_POINTS_SYSTEM, "Task: {task}"); text after "**Key Points**:" (else after
                     the last "Key Points:"), blank lines collapsed, each line left-stripped
    2. screenshots:  one call per trajectory screenshot (IMAGE_SYSTEM + task + key points + the JPEG,
                     detail "high"); score = first [1-5] after the first "Score", 0 if unparsable; a
                     screenshot is key if score >= score_threshold (3); its reasoning = first paragraph
                     after "**Reasoning**:"; at most MAX_IMAGE (50) key screenshots, in trajectory order
    3. outcome:      one call (OUTCOME_SYSTEM + task + key points + numbered action history + numbered
                     reasons + the key screenshots; the prompt without the reasons when none is key)
    success = 1 iff "success" is in the lower-cased text after the first "status:", else 0 (also 0 when
    there is no "status:")
Run success rate = successes / all task directories (upstream parallel_eval).
"""
from __future__ import annotations

import base64
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import prompts as P
from .backends import BackendError
from .cost import CapReached
from .layout import Episode

MAX_IMAGE = 50
SCORE_THRESHOLD = 3


# ---------------------------------------------------------------- upstream helpers, same rules

def encode_image(path: Path) -> str:
    """upstream utils.encode_image: PIL image -> JPEG (Pillow defaults) -> base64. RGBA -> RGB as upstream;
    other non-RGB/L modes are converted too (deviation 4: upstream crashes on them)."""
    from PIL import Image                           # lazy: only real runs need Pillow
    with Image.open(path) as image:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def parse_key_points(raw: str) -> str:
    key_points = (raw or "").replace("\n\n", "\n")
    if "**Key Points**:" in key_points:
        key_points = key_points.split("**Key Points**:")[1]
    else:
        key_points = key_points.split("Key Points:")[-1]
    return "\n".join(line.lstrip() for line in key_points.splitlines())


def parse_image_response(response: str) -> Tuple[int, Optional[str]]:
    """(score, reasoning). Upstream's try block: score_text = split("Score")[1]; thought = first paragraph
    after "**Reasoning**:"; score = first [1-5] in score_text. Any failure -> (0, None)."""
    try:
        score_text = response.split("Score")[1]
        thought = response.split("**Reasoning**:")[-1].strip().lstrip("\n").split("\n\n")[0].replace("\n", " ")
        score = re.findall(P.SCORE_PATTERN, score_text)[0]
        return int(score), thought
    except Exception:
        return 0, None


def extract_predication(response: str) -> int:
    """upstream utils.extract_predication for WebJudge_Online_Mind2Web_eval."""
    try:
        return 1 if "success" in (response or "").lower().split("status:")[1] else 0
    except Exception:
        return 0


# ---------------------------------------------------------------- messages

def key_points_messages(task: str) -> list:
    return [{"role": "system", "content": P.KEY_POINTS_SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": P.KEY_POINTS_PROMPT.format(task=task)}]}]


def image_messages(task: str, key_points: str, jpg_b64: str) -> list:
    text = P.IMAGE_PROMPT.format(task=task, key_points=key_points)
    return [{"role": "system", "content": P.IMAGE_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{jpg_b64}", "detail": "high"}}]}]


def outcome_messages(task: str, actions: List[str], key_points: str, key_images: List[str],
                     thoughts: List[str]) -> Tuple[list, str]:
    prompt = P.OUTCOME_PROMPT if key_images else P.OUTCOME_PROMPT_NO_IMAGES
    text = prompt.format(task=task, last_actions="\n".join(f"{i+1}. {a}" for i, a in enumerate(actions)),
                         key_points=key_points, thoughts="\n".join(f"{i+1}. {t}" for i, t in enumerate(thoughts)))
    # upstream labels the key screenshots image/png although encode_image produced JPEG bytes; kept as is
    imgs = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}", "detail": "high"}}
            for b in key_images]
    return [{"role": "system", "content": P.OUTCOME_SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": text}] + imgs}], text


def select_key_screenshots(responses: List[str], score_threshold: int = SCORE_THRESHOLD):
    """-> (record, key indices, reasons). Mirrors upstream's loop: a key screenshot always contributes its
    image; its reason only when non-empty, so images and reasons can differ in count (as upstream)."""
    record, keys, reasons = [], [], []
    for i, response in enumerate(responses):
        score, thought = parse_image_response(response)
        record.append({"Response": response, "Score": score})
        if score >= score_threshold:
            keys.append(i)
            if thought != "":
                reasons.append(thought)
    return record, keys[:MAX_IMAGE], reasons[:MAX_IMAGE]


# ---------------------------------------------------------------- one episode

def judge_episode(ep: Episode, backend, *, score_threshold: int = SCORE_THRESHOLD,
                  encode: Optional[Callable[[Path], str]] = None, image_workers: int = 8) -> Dict:
    """Judge one episode. Raises CapReached / BackendError; the caller records them."""
    encode = encode or encode_image
    kp_raw = backend.generate(key_points_messages(ep.task), "key_points")
    key_points = parse_key_points(kp_raw)
    b64 = [encode(p) for p in ep.screenshots]
    msgs = [image_messages(ep.task, key_points, b) for b in b64]
    if msgs:
        with ThreadPoolExecutor(max_workers=max(1, min(image_workers, len(msgs)))) as ex:
            responses = list(ex.map(lambda m: backend.generate(m, "image"), msgs))
    else:
        responses = []
    record, keys, reasons = select_key_screenshots(responses, score_threshold)
    for r, p in zip(record, ep.screenshots):
        r["screenshot"] = p.name
    messages, text = outcome_messages(ep.task, ep.action_history, key_points, [b64[i] for i in keys], reasons)
    response = backend.generate(messages, "outcome")
    label = extract_predication(response)
    return {"key_points": key_points, "image_judge_record": record,
            "key_screenshots": [ep.screenshots[i].name for i in keys], "input_text": text,
            "response": response, "predicted_label": label}


# ---------------------------------------------------------------- a run

def base_verdict(ep: Episode) -> Dict:
    return {"task_id": ep.task_id, "task": ep.task, "level": ep.level, "harness_status": ep.harness_status,
            "harness_note": ep.harness_note, "n_screenshots": len(ep.screenshots), "schema": ep.schema}


def judge_run(episodes: List[Episode], backend, *, score_threshold: int = SCORE_THRESHOLD,
              workers: int = 4, image_workers: int = 8, judge_blocked: bool = False,
              encode: Optional[Callable[[Path], str]] = None, done: Optional[Dict[str, Dict]] = None,
              on_verdict: Optional[Callable[[Dict], None]] = None, stop: Optional[threading.Event] = None,
              log: Callable[..., None] = print) -> Tuple[List[Dict], Dict]:
    """Judge every episode. Verdict status:
        judged       predicted_label from WebJudge (harness ok, or blocked/infra with judge_blocked)
        blocked      harness marked it blocked -> label 0, not sent to the judge
        infra_error  harness infrastructure failure -> label 0, not sent to the judge
        judge_error  the judge failed after retries (label None)
        not_judged   stopped by the cap or a fatal backend error before / while judging (label None)
    done = verdicts from an earlier pass (resume): those task_ids are reused, not re-judged."""
    done = done or {}
    stop = stop or threading.Event()
    info = {"stopped": None, "fatal": None}
    lock = threading.Lock()

    def one(ep: Episode) -> Dict:
        if ep.task_id in done:
            return done[ep.task_id]
        v = base_verdict(ep)
        if not ep.judgeable and not judge_blocked:
            v.update(status=ep.harness_status, predicted_label=0)
            return v
        if stop.is_set():
            v.update(status="not_judged", predicted_label=None, error=info["stopped"])
            return v
        try:
            v.update(judge_episode(ep, backend, score_threshold=score_threshold, encode=encode,
                                   image_workers=image_workers))
            v["status"] = "judged"
        except CapReached as e:
            with lock:
                info["stopped"] = info["stopped"] or f"cap: {e}"
            stop.set()
            v.update(status="not_judged", predicted_label=None, error=str(e))
        except BackendError as e:
            if e.fatal:
                with lock:
                    info["stopped"] = info["stopped"] or f"fatal: {e}"
                    info["fatal"] = info["fatal"] or str(e)
                stop.set()
                v.update(status="not_judged", predicted_label=None, error=str(e))
            else:
                v.update(status="judge_error", predicted_label=None, error=str(e))
        except (OSError, ValueError) as e:              # unreadable screenshot etc.
            v.update(status="judge_error", predicted_label=None, error=f"{type(e).__name__}: {e}")
        return v

    def run_one(ep: Episode) -> Dict:
        v = one(ep)
        if on_verdict and v.get("status") in ("judged", "blocked", "infra_error") and ep.task_id not in done:
            on_verdict(v)
        log(f"  {ep.task_id} {v.get('status')} label={v.get('predicted_label')}")
        return v

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        verdicts = list(ex.map(run_one, episodes))
    return verdicts, info
