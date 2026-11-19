import sys
import json
import os

# Ensure ai-service directory is on python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.face_service import get_face_service

def main():
    try:
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            with open(sys.argv[1], 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            raw = sys.stdin.read()
            data = json.loads(raw)

        svc = get_face_service()
        result = svc.verify_face(
            query_image_base64=data.get('query_image'),
            candidates=data.get('candidates', []),
            query_images_base64=data.get('query_images')
        )
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"matched": False, "user_id": None, "id": None, "confidence": 0.0, "error": str(e)}))

if __name__ == '__main__':
    main()
