"""Consistent Technical & Operational Glossary for Explain Studio Courses."""
from __future__ import annotations

# Standard corporate and workplace safety terminology glossary
STANDARD_TERMS: dict[str, dict[str, str]] = {
    "Personal Protective Equipment": {
        "en": "Personal Protective Equipment (PPE)",
        "zh-CN": "个人防护装备 (PPE)",
        "ms": "Peralatan Perlindungan Diri (PPE)",
        "ta": "தனிப்பட்ட பாதுகாப்பு உபகரணங்கள் (PPE)",
        "bn": "ব্যক্তিগত সুরক্ষামূলক সরঞ্জাম (PPE)",
        "te": "వ్యక్తిగత రక్షణ పరికరాలు (PPE)",
    },
    "PPE": {
        "en": "PPE",
        "zh-CN": "个人防护装备",
        "ms": "PPE",
        "ta": "பாதுகாப்பு உபகரணங்கள்",
        "bn": "পিপিই",
        "te": "పీపీఈ",
    },
    "Stop Work Authority": {
        "en": "Stop Work Authority",
        "zh-CN": "停工授权",
        "ms": "Kuasa Menghentikan Kerja",
        "ta": "வேலையை நிறுத்தும் அதிகாரம்",
        "bn": "কাজ বন্ধ করার অধিকার",
        "te": "పనిని ఆపివేసే అధికారం",
    },
    "Standard Operating Procedure": {
        "en": "Standard Operating Procedure (SOP)",
        "zh-CN": "标准作业程序 (SOP)",
        "ms": "Prosedur Operasi Standard (SOP)",
        "ta": "நிலையான இயக்க முறை (SOP)",
        "bn": "স্ট্যান্ডার্ড অপারেটিং পদ্ধতি (SOP)",
        "te": "ప్రామాణిక ఆపరేటింగ్ విధానం (SOP)",
    },
    "Zero Harm": {
        "en": "Zero Harm",
        "zh-CN": "零伤害目标",
        "ms": "Sifar Kecederaan",
        "ta": "பூஜ்ஜிய பாதிப்பு",
        "bn": "শূন্য ক্ষতি",
        "te": "సున్నా హాని లక్ష్యం",
    },
    "Lockout / Tagout": {
        "en": "Lockout / Tagout (LOTO)",
        "zh-CN": "上锁挂牌 (LOTO)",
        "ms": "Kunci dan Tag (LOTO)",
        "ta": "பூட்டு மற்றும் குறியிடுதல் (LOTO)",
        "bn": "লকআউট এবং ট্যাগআউট (LOTO)",
        "te": "లాకౌట్ / ట్యాగౌట్ (LOTO)",
    },
    "Emergency Assembly Point": {
        "en": "Emergency Assembly Point",
        "zh-CN": "紧急集合点",
        "ms": "Tempat Berkumpul Kecemasan",
        "ta": "அவசரக் கூடல் புள்ளி",
        "bn": "জরুরি সমাবেশ স্থান",
        "te": "అత్యవసర సమావేశ స్థలం",
    },
    "Job Hazard Analysis": {
        "en": "Job Hazard Analysis (JHA)",
        "zh-CN": "工作危害分析 (JHA)",
        "ms": "Analisis Bahaya Kerja (JHA)",
        "ta": "பணி ஆபத்து பகுப்பாய்வு (JHA)",
        "bn": "কাজের ঝুঁকি বিশ্লেষণ (JHA)",
        "te": "ఉద్యోగ ప్రమాదాల విశ్లేషణ (JHA)",
    },
    "Near Miss": {
        "en": "Near Miss",
        "zh-CN": "未遂事件",
        "ms": "Hampir Kemalangan",
        "ta": "விபத்தில்லாத சம்பவம் (Near Miss)",
        "bn": "আসন্ন বিপদ বা অল্পের জন্য রক্ষা",
        "te": "తృటిలో తప్పిన ప్రమాదం",
    },
    "Good Housekeeping": {
        "en": "Good Housekeeping",
        "zh-CN": "良好内务整理",
        "ms": "Pengemasan Baik",
        "ta": "சரியான பணியிடப் பராமரிப்பு",
        "bn": "উন্নত কর্মক্ষেত্র ব্যবস্থাপনা",
        "te": "మంచి కార్యాలయ నిర్వహణ",
    },
}


def build_course_glossary(slide_texts: list[str]) -> dict[str, dict[str, str]]:
    """Identify key technical terms present in the presentation and build a unified course glossary."""
    combined = " ".join(slide_texts).lower()
    course_glossary: dict[str, dict[str, str]] = {}
    for term, translations in STANDARD_TERMS.items():
        if term.lower() in combined:
            course_glossary[term] = translations
    if not course_glossary:
        # Include baseline safety terms
        for term in ["Personal Protective Equipment", "Standard Operating Procedure", "Zero Harm"]:
            course_glossary[term] = STANDARD_TERMS[term]
    return course_glossary
