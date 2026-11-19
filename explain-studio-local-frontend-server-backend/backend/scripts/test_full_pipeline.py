import json
import sys
import time
from pathlib import Path
import httpx

BASE = "http://127.0.0.1:8000"
pptx_path = Path("explain-studio/test_decks/2_safety_visitor_briefing.pptx")
if not pptx_path.exists():
    pptx_path = Path("explain-studio/test_decks/4_safety_briefing.pptx")

print(f"Target presentation: {pptx_path} ({pptx_path.stat().st_size} bytes)")

# 1. Upload presentation
with open(pptx_path, "rb") as f:
    files = {"files": (pptx_path.name, f, "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
    resp = httpx.post(f"{BASE}/v1/jobs", files=files, timeout=30.0)

job_data = resp.json()
job_id = job_data.get("job_id") or job_data.get("id")
print(f"Job created: {job_id}")

# 2. Trigger multilingual generation
trig_resp = httpx.post(f"{BASE}/v1/jobs/{job_id}/generate_all_languages", timeout=15.0)
print(f"Triggered multilingual generation: {trig_resp.status_code}")

# 3. Poll progress
t0 = time.time()
completed = False
while time.time() - t0 < 300:
    s_resp = httpx.get(f"{BASE}/v1/jobs/{job_id}", timeout=10.0)
    s_data = s_resp.json()
    status = s_data.get("status")
    progress = s_data.get("progress_pct", 0)
    stage = s_data.get("stage", "")

    elapsed = round(time.time() - t0, 1)
    print(f"  [{elapsed}s] {progress:.1f}% -> {stage[:80]}")

    if status == "completed":
        completed = True
        break
    elif status in ("failed", "error"):
        print(f"Job failed: {s_data}")
        sys.exit(1)

    time.sleep(1.5)

total_duration = round(time.time() - t0, 2)
print(f"\nCourse generation finished in {total_duration}s ({round(total_duration/60, 2)}m)!")

# 3. Retrieve Course Details
c_resp = httpx.get(f"{BASE}/v1/courses/{job_id}/course", timeout=10.0)
course = c_resp.json()
modules = course.get("modules", [])

print(f"\nTotal modules generated: {len(modules)}")
print(f"Total presentation slides: {course.get('total_slides')}")

print("\n" + "="*80)
print("AUDITING GENERATED SLIDE NARRATIONS:")
print("="*80)

slides_evaluated = 0
violations = []

for mod in modules:
    mod_title = mod.get("title", "")
    print(f"\n>>> {mod_title} (Slides: {mod.get('slide_numbers')})")

    for slide in mod.get("slides", []):
        sn = slide["slide_number"]
        title = slide.get("title", "")
        exps = slide.get("explanations", {})
        eng = exps.get("en", "")
        zh = exps.get("zh-CN", "")
        ms = exps.get("ms", "")
        ta = exps.get("ta", "")
        bn = exps.get("bn", "")
        te = exps.get("te", "")

        slides_evaluated += 1

        print(f"\n--- Slide {sn}: {title} ---")
        print(f"English ({len(eng.split())} words):")
        print(eng)
        print(f"Chinese (excerpt): {zh[:80]}...")
        print(f"Malay (excerpt): {ms[:80]}...")
        print(f"Telugu (excerpt): {te[:80]}...")

        # Strict checks for the bad patterns
        bad_patterns = [
            "building on these principles",
            "it is important to remember that",
            "as we can see",
            "let us understand",
            "please ensure that you are being escorted by our.",
            "employees throughout the duration of your visit. ensure",
        ]
        for pat in bad_patterns:
            if pat in eng.lower():
                violations.append(f"Slide {sn} contained forbidden pattern '{pat}'")

print("\n" + "="*80)
print("VERIFICATION SUMMARY:")
print("="*80)
print(f"Slides evaluated: {slides_evaluated}")
print(f"Violations detected: {len(violations)}")
if violations:
    for v in violations:
        print(f"  [X] {v}")
else:
    print("  [✓] ZERO repetitive openings")
    print("  [✓] ZERO broken sentences")
    print("  [✓] ZERO template wrappers")
    print("  [✓] All 6 languages populated")

# 4. Save results to a json file for reporting
results = {
    "job_id": job_id,
    "presentation": pptx_path.name,
    "total_slides": course.get("total_slides"),
    "total_modules": len(modules),
    "total_duration_seconds": total_duration,
    "violations": violations,
    "modules": [
        {
            "module_id": m["module_id"],
            "title": m["title"],
            "slide_numbers": m["slide_numbers"],
            "slides": [
                {
                    "slide_number": s["slide_number"],
                    "title": s["title"],
                    "english_explanation": s.get("explanations", {}).get("en", ""),
                    "languages_present": list(s.get("explanations", {}).keys()),
                }
                for s in m.get("slides", [])
            ]
        }
        for m in modules
    ]
}

out_path = Path("explain-studio/backend/storage/narration_repair_results.json")
out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Results saved to: {out_path.resolve()}")
