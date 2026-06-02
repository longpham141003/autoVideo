from __future__ import annotations

import json
import re
import wave
import math
from pathlib import Path


STRICT_NO_TEXT_IMAGE_RULE = (
    "STRICT NO-TEXT IMAGE: absolutely no visible words, letters, numbers, handwriting, typed text, captions, subtitles, signs, banners, labels, logos, watermarks, app UI text, document text, phone-screen text, or computer-screen text. "
    "If a phone, computer, document, paper, folder, plaque, flyer, banner, sign, invitation, schedule, screenshot, or social-media screen appears, its surface must be blank, turned away, overexposed, or blurred into abstract rectangles with zero readable characters."
)

FLOW_IMAGE_QUALITY_RULE = (
    "Ultra-realistic live-action photograph, natural human skin texture, realistic camera lens, cinematic natural light, 16:9. "
    "Not cartoon, not anime, not illustration, not CGI, not 3D render, not painted, not plastic skin. "
    "No visible text, letters, numbers, captions, subtitles, logos, watermarks, app UI text, document text, phone-screen text, or social-media UI; screens and papers must be blank, turned away, overexposed, or blurred into abstract unreadable shapes."
)

CHARACTER_REFERENCE_IMAGE_RULE = (
    "Character reference image, ultra-realistic live-action photograph, clean neutral composition, accurate human anatomy, natural skin texture, realistic camera lens, cinematic soft light, 16:9. "
    "Create exactly one single composite reference sheet in one image, not four separate outputs. "
    "Show only the named character being defined, repeated consistently across four clearly separated views inside the same image: front-facing head-and-shoulders portrait, left three-quarter portrait, right three-quarter portrait, and full-body standing reference. "
    "Keep the same face, hair, body type, age, wardrobe palette, and identity consistent across all four views for future scene generation. "
    "The only visible readable text allowed is the exact full character name as one clean caption centered along the bottom edge of the image; no other words, letters, numbers, logos, or watermarks anywhere else."
)


def prepare_veo_prompt_file(
    project_dir: str | Path,
    limit: int = 160,
    *,
    character_consistency: bool = True,
) -> tuple[list[str], Path]:
    project = Path(project_dir)
    timeline_prompts = build_voice_anchored_image_prompts(project, limit=limit)
    if timeline_prompts:
        if character_consistency:
            timeline_prompts = apply_targeted_character_continuity(timeline_prompts, project)
        timeline_prompts = [finalize_flow_image_prompt(prompt) for prompt in timeline_prompts]
        out_dir = project / "veo_videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "image_prompts_for_flow.txt"
        out_path.write_text("\n".join(timeline_prompts).strip() + "\n", encoding="utf-8")
        return timeline_prompts, out_path

    source_path = find_prompt_source(project)
    if source_path.exists():
        prompts = extract_direct_veo_prompts(
            source_path.read_text(encoding="utf-8", errors="ignore"),
            limit=limit,
        )
        if not prompts:
            prompts = [
                normalize_still_image_prompt(prompt)
                for prompt in extract_veo_prompts(source_path.read_text(encoding="utf-8", errors="ignore"), limit=limit)
            ]
        if not prompts:
            raise RuntimeError(f"Khong tach duoc prompt anh nao tu {source_path.name}")
        prompts = reduce_prompts_to_one_per_chapter(prompts, project, limit)
        if character_consistency:
            prompts = apply_targeted_character_continuity(prompts, project)
        prompts = [finalize_flow_image_prompt(prompt) for prompt in prompts]
        out_dir = project / "veo_videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "image_prompts_for_flow.txt"
        out_path.write_text("\n".join(prompts).strip() + "\n", encoding="utf-8")
        return prompts, out_path

    raise FileNotFoundError(f"Chua co file prompt anh: {source_path}")


def prepare_character_reference_prompt_file(
    project_dir: str | Path,
    limit: int = 64,
) -> tuple[list[str], Path | None]:
    project = Path(project_dir)
    max_count = max(1, min(int(limit or 64), 200))
    source = project / "artifacts" / "character_reference_prompts.txt"
    prompts: list[str] = []
    if source.exists():
        raw = source.read_text(encoding="utf-8", errors="ignore")
        prompts = extract_direct_veo_prompts(raw, limit=max_count)
        if not prompts:
            prompts = extract_veo_prompts(raw, limit=max_count)
    if not prompts:
        prompts = build_character_reference_prompts_from_bible(project, limit=max_count)
    if not prompts:
        return [], None

    prompts = [finalize_character_reference_prompt(prompt) for prompt in prompts[:max_count]]
    out_dir = project / "veo_videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "character_reference_prompts_for_flow.txt"
    out_path.write_text("\n".join(prompts).strip() + "\n", encoding="utf-8")
    return prompts, out_path


def build_character_reference_prompts_from_bible(project: Path, limit: int = 64) -> list[str]:
    entries = parse_project_character_bible(project)
    prompts: list[str] = []
    for name, description in entries.items():
        prompt = (
            f"{name}: create one single composite character reference sheet for {description}. "
            f"Inside that one 16:9 image, arrange four clearly separated views of {name}: "
            "front-facing head-and-shoulders portrait with direct eye contact; "
            "left three-quarter portrait showing facial structure and hairline; "
            "right three-quarter portrait showing facial structure and hairline; "
            "full-body standing reference showing wardrobe palette, build, and posture. "
            "All four views must depict the exact same person with consistent identity and styling, not variants or different people. "
            f"Place the exact readable caption \"{name}\" centered along the bottom edge of the overall image only once."
        )
        prompts.append(prompt)
        if len(prompts) >= max(1, int(limit or 64)):
            return prompts
    return prompts


def build_voice_anchored_image_prompts(project: Path, limit: int = 160) -> list[str]:
    scripts = sorted((project / "scripts").glob("chapter_*.txt"), key=natural_key)
    if not scripts:
        return []
    voices = sorted((project / "voices").glob("chapter_*.*"), key=natural_key)
    voices = [path for path in voices if path.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".webm"}]
    total_duration = sum(voice_duration_seconds(path) for path in voices)
    if total_duration <= 0:
        return []
    max_count = max(1, min(int(limit or 160), 500))
    target_count = max(1, min(max_count, int(math.ceil(total_duration / 60.0))))
    blocks = collect_minute_voice_blocks(project, scripts, voices, total_duration, target_count)
    if not blocks:
        return []
    prompts = [image_prompt_for_block(block) for block in blocks[:max_count]]
    timeline_path = project / "veo_videos" / "image_prompt_timeline.json"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text(
        json.dumps({"version": 1, "source": "minute_voice_blocks", "blocks": blocks[:max_count]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return prompts


def voice_duration_seconds(path: Path) -> float:
    segments = read_voice_segments_for_path(path)
    if segments:
        try:
            return max(float(segment.get("end") or 0.0) for segment in segments)
        except Exception:
            pass
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
                return float(frames) / float(rate) if rate > 0 else 0.0
        except Exception:
            return 0.0
    return 0.0


def read_voice_segments_for_path(voice_path: Path) -> list[dict]:
    timing_path = voice_path.with_suffix(".segments.json")
    if not timing_path.exists():
        return []
    try:
        data = json.loads(timing_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    segments = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segments, list):
        return []
    clean: list[dict] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = clean_script_for_prompt(str(segment.get("text") or ""))
        if not text:
            continue
        try:
            start = max(0.0, float(segment.get("start") or 0.0))
            end = max(start, float(segment.get("end") or start))
        except Exception:
            continue
        clean.append({"text": text, "start": start, "end": end})
    return clean


def collect_minute_voice_blocks(
    project: Path,
    scripts: list[Path],
    voices: list[Path],
    total_duration: float,
    target_count: int,
) -> list[dict]:
    segments: list[dict] = []
    cursor = 0.0
    for chapter_index, voice in enumerate(voices, start=1):
        chapter_duration = voice_duration_seconds(voice)
        for segment in read_voice_segments_for_path(voice):
            segments.append(
                {
                    "chapter": chapter_index,
                    "start": cursor + float(segment.get("start") or 0.0),
                    "end": cursor + float(segment.get("end") or 0.0),
                    "text": str(segment.get("text") or ""),
                }
            )
        cursor += max(0.0, chapter_duration)

    if not segments:
        return collect_estimated_minute_blocks(scripts, total_duration, target_count)

    blocks: list[dict] = []
    for index in range(target_count):
        start = min(total_duration, index * 60.0)
        end = min(total_duration, (index + 1) * 60.0)
        if index == target_count - 1:
            end = total_duration
        overlapping = [
            segment
            for segment in segments
            if float(segment.get("end") or 0.0) > start and float(segment.get("start") or 0.0) < end
        ]
        if not overlapping:
            center = start + (end - start) / 2.0
            overlapping = [
                min(
                    segments,
                    key=lambda item: abs(((float(item.get("start") or 0.0) + float(item.get("end") or 0.0)) / 2.0) - center),
                )
            ]
        text = " ".join(str(item.get("text") or "") for item in overlapping)
        chapter = int(overlapping[0].get("chapter") or 1) if overlapping else 1
        blocks.append(
            {
                "chapter": chapter,
                "voice_excerpt": visual_safe_story_context(text, 760),
                "start": round(start, 3),
                "end": round(end, 3),
            }
        )
    return blocks


def collect_estimated_minute_blocks(scripts: list[Path], total_duration: float, target_count: int) -> list[dict]:
    text = " ".join(
        clean_script_for_prompt(path.read_text(encoding="utf-8", errors="ignore"))
        for path in scripts
    )
    sentences = split_sentences(text)
    if not sentences:
        return []
    total_words = sum(max(1, len(sentence.split())) for sentence in sentences)
    words_per_block = max(1, math.ceil(total_words / max(1, target_count)))
    blocks: list[dict] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        current.append(sentence)
        current_words += max(1, len(sentence.split()))
        if current_words >= words_per_block and len(blocks) < target_count - 1:
            index = len(blocks)
            blocks.append(
                {
                    "chapter": 1,
                    "voice_excerpt": visual_safe_story_context(" ".join(current), 760),
                    "start": round(total_duration * index / target_count, 3),
                    "end": round(total_duration * (index + 1) / target_count, 3),
                }
            )
            current = []
            current_words = 0
    if current:
        index = len(blocks)
        blocks.append(
            {
                "chapter": 1,
                "voice_excerpt": visual_safe_story_context(" ".join(current), 760),
                "start": round(total_duration * index / target_count, 3),
                "end": round(total_duration, 3),
            }
        )
    return blocks[:target_count]


def infer_chapter_prompt_count(project: Path, fallback: int = 8, limit: int | None = None) -> int:
    count = 0
    input_path = Path(project) / "input.json"
    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            count = int(data.get("chapter_count") or 0)
    except Exception:
        count = 0
    if count <= 0:
        count = int(fallback or 1)
    if limit is not None:
        try:
            count = min(count, max(1, int(limit or count)))
        except Exception:
            pass
    return max(1, min(count, 20))


def reduce_prompts_to_one_per_chapter(prompts: list[str], project: Path, limit: int = 160) -> list[str]:
    if not prompts:
        return []
    chapter_count = infer_chapter_prompt_count(project, fallback=len(prompts), limit=limit)
    if len(prompts) <= chapter_count:
        return prompts[:chapter_count]

    numbered: dict[int, str] = {}
    for prompt in prompts:
        match = re.search(r"\bchapter\s*(\d{1,2})\b", prompt, flags=re.IGNORECASE)
        if not match:
            match = re.search(r"\bchuong\s*(\d{1,2})\b", prompt, flags=re.IGNORECASE)
        if not match:
            match = re.search(r"\bchương\s*(\d{1,2})\b", prompt, flags=re.IGNORECASE)
        if match:
            index = int(match.group(1))
            if 1 <= index <= chapter_count and index not in numbered:
                numbered[index] = prompt
    if len(numbered) == chapter_count:
        return [numbered[index] for index in range(1, chapter_count + 1)]

    selected: list[str] = []
    for index in range(chapter_count):
        source_index = round(index * (len(prompts) - 1) / max(1, chapter_count - 1))
        selected.append(prompts[int(source_index)])
    return selected


def collect_chapter_representative_blocks(project: Path, scripts: list[Path], max_count: int) -> list[dict]:
    blocks: list[dict] = []
    for chapter_index, script_path in enumerate(scripts[:max_count], start=1):
        text = clean_script_for_prompt(script_path.read_text(encoding="utf-8", errors="ignore"))
        if not text:
            continue
        blocks.append(
            {
                "chapter": int(chapter_index),
                "voice_excerpt": representative_chapter_excerpt(text),
                "start": 0.0,
                "end": 0.0,
            }
        )
    return blocks


def representative_chapter_excerpt(text: str, limit: int = 760) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return trim_excerpt(text, limit)
    scored: list[tuple[int, int, str]] = []
    keywords = re.compile(
        r"\b(?:wedding|phone|message|call|missed|bank|lawyer|legal|court|document|papers|"
        r"money|dollar|account|family|father|mother|sister|brother|office|house|door|"
        r"proof|evidence|betrayal|confront|locked|payment|notice|gala|dinner)\b",
        re.IGNORECASE,
    )
    for index, sentence in enumerate(sentences):
        words = len(sentence.split())
        if words < 8:
            continue
        score = len(keywords.findall(sentence))
        score += 3 if re.search(r"[\"']", sentence) else 0
        score += 2 if re.search(r"\b\d[\d,.:]*\b", sentence) else 0
        scored.append((score, -index, sentence))
    if scored:
        _score, neg_index, sentence = max(scored)
        index = -neg_index
    else:
        index = min(len(sentences) - 1, max(0, len(sentences) // 2))
        sentence = sentences[index]
    context = " ".join(sentences[max(0, index - 1) : min(len(sentences), index + 2)])
    return trim_excerpt(context or sentence, limit)


def collect_voice_blocks(project: Path, scripts: list[Path], max_count: int) -> list[dict]:
    target_words = 65
    blocks: list[dict] = []
    for chapter_index, script_path in enumerate(scripts, start=1):
        segments = read_chapter_segments(project, chapter_index)
        if segments:
            blocks.extend(group_timing_segments(chapter_index, segments, target_words=target_words))
            continue
        text = clean_script_for_prompt(script_path.read_text(encoding="utf-8", errors="ignore"))
        blocks.extend(group_text_sentences(chapter_index, text, target_words=target_words))
    if len(blocks) <= max_count:
        return blocks
    return merge_blocks_to_count(blocks, max_count)


def read_chapter_segments(project: Path, chapter_index: int) -> list[dict]:
    timing_path = project / "voices" / f"chapter_{int(chapter_index):02d}.segments.json"
    if not timing_path.exists():
        return []
    try:
        data = json.loads(timing_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    segments = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segments, list):
        return []
    clean_segments: list[dict] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = clean_script_for_prompt(str(segment.get("text") or ""))
        if not text:
            continue
        try:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or start)
        except Exception:
            start = 0.0
            end = 0.0
        clean_segments.append({"text": text, "start": max(0.0, start), "end": max(start, end)})
    return clean_segments


def group_timing_segments(chapter_index: int, segments: list[dict], target_words: int = 65) -> list[dict]:
    blocks: list[dict] = []
    current: list[dict] = []
    current_words = 0
    for segment in segments:
        words = len(str(segment.get("text") or "").split())
        current.append(segment)
        current_words += words
        duration = float(current[-1].get("end") or 0.0) - float(current[0].get("start") or 0.0)
        if current_words >= target_words or duration >= 24.0:
            blocks.append(timing_block(chapter_index, current))
            current = []
            current_words = 0
    if current:
        blocks.append(timing_block(chapter_index, current))
    return blocks


def timing_block(chapter_index: int, segments: list[dict]) -> dict:
    text = " ".join(str(segment.get("text") or "").strip() for segment in segments if str(segment.get("text") or "").strip())
    return {
        "chapter": int(chapter_index),
        "voice_excerpt": trim_excerpt(text, 520),
        "start": round(float(segments[0].get("start") or 0.0), 3) if segments else 0.0,
        "end": round(float(segments[-1].get("end") or 0.0), 3) if segments else 0.0,
    }


def group_text_sentences(chapter_index: int, text: str, target_words: int = 65) -> list[dict]:
    sentences = split_sentences(text)
    blocks: list[dict] = []
    current: list[str] = []
    words = 0
    for sentence in sentences:
        current.append(sentence)
        words += len(sentence.split())
        if words >= target_words:
            blocks.append({"chapter": int(chapter_index), "voice_excerpt": trim_excerpt(" ".join(current), 520)})
            current = []
            words = 0
    if current:
        blocks.append({"chapter": int(chapter_index), "voice_excerpt": trim_excerpt(" ".join(current), 520)})
    return blocks


def merge_blocks_to_count(blocks: list[dict], max_count: int) -> list[dict]:
    if len(blocks) <= max_count:
        return blocks
    merged: list[dict] = []
    bucket = len(blocks) / float(max_count)
    cursor = 0.0
    for _ in range(max_count):
        start = int(round(cursor))
        cursor += bucket
        end = int(round(cursor))
        group = blocks[start:max(end, start + 1)]
        if not group:
            continue
        text = " ".join(str(item.get("voice_excerpt") or "") for item in group)
        merged.append(
            {
                "chapter": group[0].get("chapter"),
                "voice_excerpt": trim_excerpt(text, 620),
                "start": group[0].get("start", 0.0),
                "end": group[-1].get("end", 0.0),
            }
        )
    return merged


def image_prompt_for_block(block: dict) -> str:
    excerpt = visual_safe_story_context(str(block.get("voice_excerpt") or ""), 500)
    prompt = (
        "16:9 ultra-realistic cinematic still image for an American family revenge storytelling video. "
        f"Story beat to visualize, context only and never as visible text: {excerpt}. "
        "Show one frozen story moment directly implied by the story beat, with the correct characters, location, emotional tension, and a concrete proof object or action if mentioned. "
        "Use phones, documents, screenshots, flyers, banners, and papers only as blank or blurred props; the viewer should understand the situation from faces, posture, setting, and objects, not from written words. "
        "Keep character continuity across the full story: consistent ages, hair, wardrobe palette, social class, and relationships. "
        "Do not invent unrelated scenes, landscapes, random luxury imagery, or new characters. "
        f"{STRICT_NO_TEXT_IMAGE_RULE}"
    )
    return enforce_no_text_image_prompt(prompt)


def visual_safe_story_context(text: str, limit: int = 500) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return "a tense family drama moment with clear emotion and no written content"

    value = re.sub(r"\"[^\"]{1,240}\"", "a short spoken line", value)
    value = re.sub(r"'[^']{1,240}'", "a short spoken line", value)
    replacements = [
        (r"\bcaption\s+(?:said|says|read|reads)\s*,?\s*[^.?!]{0,180}", "a social-media post area is fully blurred"),
        (r"\bflyer\s+(?:said|says|read|reads)\s*,?\s*[^.?!]{0,180}", "a blank event flyer shape is visible"),
        (r"\bmessage\s+(?:said|says|read|reads)\s*,?\s*[^.?!]{0,180}", "a blank phone message bubble is visible"),
        (r"\btext\s+(?:said|says|read|reads|came through)\s*,?\s*[^.?!]{0,180}", "a blank phone notification is visible"),
        (r"\bphone message on screen\b", "blank phone screen"),
        (r"\bphone message\b", "blank phone notification"),
        (r"\bmessage bubbles\b", "blank phone bubbles"),
        (r"\b(?:comment|comments)\s+(?:underneath|said|says|read|reads|wrote)\s*,?\s*[^.?!]{0,180}", "blurred social-media comment rows are visible"),
        (r"\bone woman actually wrote\s*,?\s*[^.?!]{0,180}", "one blurred comment row stands out"),
        (r"\bcalled\s+PROOF\b", "with a blank title area"),
        (r"\bfolder\s+called\s+[^.?!]{1,80}", "phone folder with a blank title area"),
        (r"\bFather Daughter Entrance\b", "one blue-highlighted schedule row"),
        (r"\bwith\s+[^.?!]{0,80}\s+highlighted\s+in\s+blue\b", "with one blue-highlighted blank row"),
        (r"\bphone\s+showed\s+[^.?!]{0,120}\s+missed calls\b", "phone call screen shows blurred notification rows"),
        (r"\bcall log\b", "blank phone call-list screen"),
        (r"\bmissed calls?\b", "blurred call notifications"),
        (r"\bInstagram\b", "social media"),
        (r"\bNotes app\b", "phone notes app with a blank screen"),
        (r"\bscreenshot[s]?\b", "blurred phone evidence image"),
        (r"\bcharity banner\b", "blank event backdrop"),
        (r"\bgold lettering\b", "gold decorative shapes"),
        (r"\bplaque\b", "blank award plaque"),
        (r"\bwedding papers\b", "blank wedding papers"),
        (r"\bwedding paperwork\b", "blank wedding papers"),
        (r"\bpaperwork\b", "blank papers"),
        (r"\bpapers visible\b", "blank papers visible"),
        (r"\b(?:legal|bank|court|financial)\s+documents\b", "blank/blurred documents"),
        (r"\bPROOF\b", "blank evidence folder"),
        (r"\bGALA\b", "charity event"),
        (r"\bCovenant Breach Notice\b", "blank legal notice"),
        (r"\bsubject line\s+[^.?!]{0,120}", "blank subject area"),
        (r"\bemail\s+from\s+[^.?!]{0,120}", "blank email alert"),
        (r"\btranscript\b", "blank transcript page"),
        (r"\bvenue text\b", "blank venue message"),
        (r"\bschedule\b", "blank schedule"),
        (r"\btimeline\b", "blank timeline page"),
        (r"\bflyer\b", "blank flyer"),
        (r"\bbanner\b", "blank backdrop"),
        (r"\bmonitor\b", "blank monitor"),
        (r"\bcaption-safe\b", "empty lower area"),
    ]
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)

    value = re.sub(
        r"\b(?:he|she|they|dad|mom|father|mother|my father|my mother|richard|monica|cole|gavin|danielle)\s+"
        r"(?:said|asked|continued|joked|replied|admitted)\s*,?\s*[^.?!]{0,180}",
        "a tense conversation is implied",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\b(?:the\s+)?(?:caption|message|text|post|comment|flyer|banner|document|paper|screen|app)\s+(?:said|says|read|reads|shows|showed)\b", "the visual prop implies", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:word|words|lettering|letters|numbers|readable text|typed text|handwriting)\b", "blank visual detail", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:16:9|9:16)\b", "", value)
    value = re.sub(r"\b\d[\d: ]*\b", "several", value)
    value = re.sub(r"\ban social\b", "a social", value, flags=re.IGNORECASE)
    value = re.sub(r"\bThe a social\b", "A social", value)
    value = re.sub(r"\bone blue-highlighted schedule row highlighted in blue\b", "one blue-highlighted blank schedule row", value, flags=re.IGNORECASE)
    value = re.sub(r"\bblank wedding blank papers\b", "blank wedding papers", value, flags=re.IGNORECASE)
    value = re.sub(r"\brehearsal blank timeline page\b", "blank rehearsal timeline page", value, flags=re.IGNORECASE)
    value = re.sub(r"\bblank timeline page had one blue-highlighted blank schedule row highlighted in blue\b", "blank timeline page with one blue-highlighted blank schedule row", value, flags=re.IGNORECASE)
    value = re.sub(r"\bone blue-highlighted blank schedule row highlighted in blue\b", "one blue-highlighted blank schedule row", value, flags=re.IGNORECASE)
    value = re.sub(r"\bempty lower area lower area\b", "empty lower area", value, flags=re.IGNORECASE)
    value = re.sub(r"\bblank evidence folder folder\b", "blank evidence folder", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    return trim_excerpt(value, limit)


def enforce_no_text_image_prompt(prompt: str) -> str:
    value = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if STRICT_NO_TEXT_IMAGE_RULE not in value:
        value = f"{value} {STRICT_NO_TEXT_IMAGE_RULE}"
    return value.strip()


def finalize_flow_image_prompt(prompt: str) -> str:
    value = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if not value:
        return value
    lowered = value.lower()
    if "ultra-realistic live-action photograph" not in lowered:
        value = f"{value} {FLOW_IMAGE_QUALITY_RULE}"
    return value.strip()


def finalize_character_reference_prompt(prompt: str) -> str:
    value = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if not value:
        return value
    lowered = value.lower()
    if "character reference image" not in lowered:
        value = f"{value} {CHARACTER_REFERENCE_IMAGE_RULE}"
    elif CHARACTER_REFERENCE_IMAGE_RULE not in value:
        value = f"{value} {CHARACTER_REFERENCE_IMAGE_RULE}"
    return value.strip()


def clean_script_for_prompt(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    json_text = extract_prompt_text_from_json_payload(value)
    if json_text:
        value = json_text
    value = re.sub(r"```(?:text|plain|markdown|md|json)?\s*", " ", value, flags=re.IGNORECASE)
    value = value.replace("```", " ")
    cleaned: list[str] = []
    skip = re.compile(
        r"^\s*(?:#{1,6}\s*.+|(?:chapter|part)\s+\d+\b.*|"
        r"(?:title|word count|script|output|image[_ -]?prompt|veo[_ -]?prompt|video[_ -]?prompt|negative[_ -]?prompt|visual[_ -]?context|caption[_ -]?overlay|metadata|duration[_ -]?seconds|blocks|seo|retention|emotional goal|mini-hook|conflict|visual|action|internal line|immediate reaction)\s*:.*)$",
        re.IGNORECASE,
    )
    metadata_line = re.compile(r"^\s*[\"']?([A-Za-z][A-Za-z0-9_ -]{0,40})[\"']?\s*:\s*(.*?)[,;]?\s*$")
    skip_keys = {
        "image prompt",
        "image prompts",
        "veo prompt",
        "veo prompts",
        "video prompt",
        "video prompts",
        "negative prompt",
        "visual",
        "visual context",
        "caption overlay",
        "prompt",
        "metadata",
        "duration seconds",
        "blocks",
        "seo",
        "retention",
    }
    keep_keys = {
        "voice",
        "voiceover",
        "voice over",
        "narrator",
        "narration",
        "script",
        "script text",
        "plain text",
        "spoken text",
        "text",
    }
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = metadata_line.match(line)
        if match:
            key = re.sub(r"\s+", " ", match.group(1).replace("_", " ")).strip().lower()
            raw_value = match.group(2).strip().strip(",").strip()
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"\"", "'"}:
                raw_value = raw_value[1:-1].strip()
            if key in skip_keys:
                continue
            if key in keep_keys:
                if not raw_value:
                    continue
                line = raw_value
        if skip.match(line):
            continue
        line = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s+", "", line)
        line = re.sub(r"[`*_#<>[\]{}]", " ", line)
        cleaned.append(line)
    value = " ".join(cleaned)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_prompt_text_from_json_payload(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"^```(?:json|text|plain|markdown|md)?\s*", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"```$", "", raw).strip()
    candidates = [raw]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if 0 <= start < end:
            candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        values = collect_prompt_json_values(data)
        if values:
            return "\n\n".join(values)
    return ""


def collect_prompt_json_values(data: object, parent_key: str = "") -> list[str]:
    keep_keys = {"voice", "voiceover", "voice over", "narrator", "narration", "script", "script text", "plain text", "spoken text", "text"}
    skip_keys = {
        "image prompt",
        "image prompts",
        "veo prompt",
        "veo prompts",
        "video prompt",
        "video prompts",
        "negative prompt",
        "visual",
        "visual context",
        "caption overlay",
        "cover image prompt",
        "prompt",
        "metadata",
        "duration seconds",
        "seo",
        "retention",
    }
    key = re.sub(r"\s+", " ", str(parent_key or "").replace("_", " ")).strip().lower()
    if key in skip_keys:
        return []
    if isinstance(data, str):
        clean = data.strip()
        return [clean] if clean and key in keep_keys else []
    if isinstance(data, dict):
        values: list[str] = []
        for child_key, child_value in data.items():
            values.extend(collect_prompt_json_values(child_value, str(child_key)))
        return values
    if isinstance(data, list):
        values: list[str] = []
        for item in data:
            values.extend(collect_prompt_json_values(item, parent_key))
        return values
    return []


def split_sentences(text: str) -> list[str]:
    value = clean_script_for_prompt(text)
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", value) if item.strip()]


def trim_excerpt(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    cut = value[: max(40, int(limit))]
    last_space = cut.rfind(" ")
    if last_space > 80:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:") + "..."


def natural_key(path: Path | str) -> list[object]:
    text = str(Path(path).name if isinstance(path, Path) else path)
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def find_prompt_source(project: Path) -> Path:
    prepared = project / "veo_videos" / "image_prompts_for_flow.txt"
    if prepared.exists():
        return prepared
    artifacts = project / "artifacts"
    preferred = artifacts / "veo3_prompts.txt"
    if preferred.exists():
        return preferred
    return artifacts / "whisk_prompts.txt"


def extract_direct_veo_prompts(text: str, limit: int = 160) -> list[str]:
    max_count = max(1, min(int(limit or 160), 500))
    prompts: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        prompt = clean_prompt_line(raw_line)
        if not is_prompt_line(prompt):
            continue
        prompts.append(prompt)
        if len(prompts) >= max_count:
            break
    return prompts


def apply_character_continuity(prompts: list[str], project: Path | None = None) -> list[str]:
    guide = read_project_character_bible(project) if project is not None else ""
    if not guide:
        guide = build_character_continuity_guide(prompts)
    if not guide:
        return prompts
    return [attach_character_guide(prompt, guide) for prompt in prompts]


def apply_targeted_character_continuity(prompts: list[str], project: Path | None = None) -> list[str]:
    entries = parse_project_character_bible(project)
    if not entries:
        return prompts
    return [attach_targeted_character_entries(prompt, entries) for prompt in prompts]


def parse_project_character_bible(project: Path | None) -> dict[str, str]:
    if project is None:
        return {}
    path = Path(project) / "artifacts" / "character_bible.txt"
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or "," not in line:
            continue
        name = line.split(",", 1)[0].strip()
        if not re.match(r"^[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}$", name):
            continue
        # Keep the identity compact so it reinforces the face without turning
        # one prompt into an ensemble cast prompt.
        entries[name] = trim_excerpt(line, 360)
    return entries


def attach_targeted_character_entries(prompt: str, entries: dict[str, str]) -> str:
    source = str(prompt or "").strip()
    if not source or "character continuity for visible named characters:" in source.lower():
        return source
    matched: list[tuple[int, str, str]] = []
    lowered = source.lower()
    for full_name, description in entries.items():
        name_parts = full_name.split()
        aliases = {full_name.lower()}
        if name_parts:
            aliases.add(name_parts[0].lower())
        positions = [
            match.start()
            for alias in aliases
            for match in re.finditer(rf"\b{re.escape(alias)}\b", lowered)
        ]
        if positions:
            matched.append((min(positions), full_name, description))
    if not matched:
        return source
    matched.sort(key=lambda item: item[0])
    names = [item[1] for item in matched[:4]]
    descriptions = [item[2] for item in matched[:4]]
    prefix = ", ".join(names) + ": "
    if not source.lower().startswith(tuple(name.lower() for name in names)):
        source = prefix + source
    guide = " Character continuity for visible named characters: " + " | ".join(descriptions) + "."
    return source + guide


def build_character_continuity_guide(prompts: list[str]) -> str:
    joined = "\n".join(str(prompt or "") for prompt in prompts)
    names = extract_likely_character_names(joined)
    if not names:
        return ""
    guide = "; ".join(f"{name} must keep the same face, age, hair, body type, wardrobe palette, and relationship role across the full batch" for name in names[:8])
    return (
        "Character continuity reference for this entire image batch: "
        f"{guide}. Do not invent replacement characters."
    )


def read_project_character_bible(project: Path | None) -> str:
    if project is None:
        return ""
    for name in ("character_bible.txt", "characters.txt", "character_continuity.txt"):
        path = Path(project) / "artifacts" / name
        if not path.exists():
            continue
        text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8", errors="ignore")).strip()
        if text:
            return f"Character continuity reference for this entire image batch: {trim_excerpt(text, 1400)}"
    return ""


def extract_likely_character_names(text: str) -> list[str]:
    stop = {
        "A",
        "An",
        "The",
        "Close",
        "Over",
        "Wide",
        "Medium",
        "Mid",
        "Side",
        "Reaction",
        "Guests",
        "Jackson",
        "Hole",
        "SUV",
        "PDF",
        "Uber",
        "Tom",
        "Ford",
    }
    counts: dict[str, int] = {}
    for match in re.finditer(r"\b([A-Z][a-z]{2,})(?:\s+([A-Z][a-z]{2,}))?\b", str(text or "")):
        name = " ".join(part for part in match.groups() if part)
        first = name.split()[0]
        if first in stop or name in stop:
            continue
        counts[name] = counts.get(name, 0) + 1
    return [name for name, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def attach_character_guide(prompt: str, guide: str) -> str:
    source = str(prompt or "").strip()
    if not source or guide.lower() in source.lower():
        return source
    return f"{guide} Image prompt: {source}"


def extract_veo_prompts(text: str, limit: int = 160) -> list[str]:
    max_count = max(1, min(int(limit or 160), 500))
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [clean_prompt_line(line) for line in raw.split("\n")]
    prompts = [line for line in lines if is_prompt_line(line)]
    if len(prompts) <= 1:
        prompts = split_single_paragraph_prompts(raw)
    return prompts[:max_count]


def clean_prompt_line(line: str) -> str:
    text = str(line or "").strip()
    text = re.sub(r"^\s*(?:[-*]\s*)?(?:prompt\s*)?\d{1,3}\s*[\).:\-]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:[-*]\s*)?(?:scene|shot|image|video)\s*\d{1,3}\s*[\).:\-]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_prompt_line(text: str) -> bool:
    if len(text) < 30:
        return False
    lowered = text.lower()
    if lowered.startswith(("here are", "below are", "sure,", "certainly,")):
        return False
    if lowered in {"prompts", "image prompts", "video prompts", "veo prompts", "veo3 prompts", "whisk prompts"}:
        return False
    return True


def split_single_paragraph_prompts(text: str) -> list[str]:
    raw = clean_prompt_line(text)
    if not raw:
        return []

    # ChatGPT sometimes ignores "one prompt per line" and returns one long
    # comma-separated storyboard. Split at cinematic shot markers and turn each
    # beat into a standalone VEO prompt.
    markers = (
        "cinematic",
        "wide shot",
        "medium shot",
        "close-up",
        "over-the-shoulder",
        "reaction shot",
        "slow dolly",
        "slow push-in",
        "gentle push-in",
        "gentle pan",
        "handheld tension",
        "handheld subtle tension",
        "rack focus",
        "papers sliding",
        "phone vibrating",
        "hands holding",
    )
    marker_re = re.compile(r"(?<!^)\s*,\s*(?=(" + "|".join(re.escape(x) for x in markers) + r")\b)", re.IGNORECASE)
    parts = [clean_prompt_line(part) for part in marker_re.split(raw)]
    prompts: list[str] = []
    for part in parts:
        if not part or len(part) < 24:
            continue
        prompt = normalize_standalone_prompt(part)
        if is_prompt_line(prompt):
            prompts.append(prompt)
    return dedupe_prompts(prompts)


def normalize_standalone_prompt(text: str) -> str:
    prompt = visual_safe_story_context(clean_prompt_line(text), 700)
    lowered = prompt.lower()
    if "16:9" not in lowered:
        prompt = f"cinematic 16:9 realistic shot, {prompt}"
    return enforce_no_text_image_prompt(prompt)


def normalize_still_image_prompt(text: str) -> str:
    prompt = visual_safe_story_context(clean_prompt_line(text), 700)
    prompt = re.sub(r"\b(?:8[- ]?second|eight[- ]?second)\s+(?:veo3?\s+)?video\s+clip\b", "still image", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\b(?:veo3?|google veo 3\.1 fast|text[- ]to[- ]video|video)\s+prompt\b", "still image prompt", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\b(?:slow dolly|slow push-in|gentle push-in|gentle pan|handheld tension|rack focus|camera movement|subtle camera movement)\b", "cinematic still composition", prompt, flags=re.IGNORECASE)
    lowered = prompt.lower()
    prefix = "16:9 ultra-realistic cinematic still image, one frozen story moment, "
    if "still image" not in lowered:
        prompt = prefix + prompt
    elif "16:9" not in lowered:
        prompt = "16:9 " + prompt
    if "frozen" not in prompt.lower():
        prompt = f"{prompt}, frozen moment, clear character emotion, context-specific location and props"
    return enforce_no_text_image_prompt(prompt)


def dedupe_prompts(prompts: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for prompt in prompts:
        key = re.sub(r"\W+", " ", prompt.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(prompt)
    return unique
