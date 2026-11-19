import sys, json, asyncio, time
sys.path.insert(0, 'explain-studio/backend')

from app.ai.multilingual.planner import _extract_slide_summaries, _build_slide_teaching_explanation
from app.extractor.pptx import extract_from_pptx_file
from pathlib import Path

# Find the PPTX
pptx_candidates = list(Path('explain-studio/backend/storage').rglob('*.pptx'))
if not pptx_candidates:
    pptx_candidates = list(Path('.').rglob('4_safety_briefing.pptx'))
pptx_path = pptx_candidates[0] if pptx_candidates else None
print(f"Testing with: {pptx_path}")

if pptx_path:
    tmp = Path('explain-studio/backend/storage/tmp/test_assets')
    tmp.mkdir(parents=True, exist_ok=True)
    blocks = extract_from_pptx_file(pptx_path, source_id='test', original_filename=pptx_path.name, assets_dir=tmp)
    manifest = {'blocks': [b.model_dump() for b in blocks]}
    slides_info = _extract_slide_summaries(manifest)
    total = max(slides_info.keys())
    
    print(f"\n=== DETERMINISTIC FALLBACK OUTPUT ({total} slides) ===")
    for sn in sorted(slides_info.keys())[:5]:  # First 5 slides
        exp = _build_slide_teaching_explanation(slides_info[sn], sn, total)
        print(f"\n--- Slide {sn}: {slides_info[sn].get('title','?')} ---")
        print(exp)
        word_count = len(exp.split())
        print(f"[words={word_count}]")

    print("\n\n=== QUALITY CHECKS ===")
    bad_openers = [
        "Building on these principles",
        "It is important to remember",
        "In this slide",
        "This slide shows",
        "Slide ",
    ]
    total_slides_checked = 0
    bad_opener_count = 0
    duplicate_sent_count = 0
    empty_count = 0

    import re
    from collections import Counter

    for sn in sorted(slides_info.keys()):
        exp = _build_slide_teaching_explanation(slides_info[sn], sn, total)
        total_slides_checked += 1
        if not exp.strip():
            empty_count += 1
        for opener in bad_openers:
            if exp.startswith(opener):
                bad_opener_count += 1
                print(f"  BAD OPENER on slide {sn}: {exp[:80]}")
        # Check for duplicate sentences
        sentences = [s.strip() for s in re.split(r'[.!?]+', exp) if len(s.strip()) > 20]
        counts = Counter(sentences)
        dupes = [s for s, c in counts.items() if c > 1]
        if dupes:
            duplicate_sent_count += 1
            print(f"  DUPE SENTENCE on slide {sn}: {dupes[0][:60]}")

    print(f"\nTotal slides: {total_slides_checked}")
    print(f"Bad openers: {bad_opener_count}")
    print(f"Slides with duplicate sentences: {duplicate_sent_count}")
    print(f"Empty explanations: {empty_count}")
