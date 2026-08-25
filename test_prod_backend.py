import urllib.request
import json

def test_production_backend():
    print("Testing production backend...")
    url = "https://burkinadt.yingr-ai.com/api/v1/translate-sentence"
    payload = {
        "text": "je pars voir mon arachide",
        "target_lang": "moore"
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'X-API-Key': 'bk_live_mobile_app_key'
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res = json.loads(response.read().decode('utf-8'))
            print("Status code:", response.status)
            print("Response:", json.dumps(res, ensure_ascii=False, indent=2))
    except Exception as e:
        print("Failed to call production backend:", e)

if __name__ == "__main__":
    test_production_backend()
