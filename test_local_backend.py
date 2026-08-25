import urllib.request
import json

def test_local_backend():
    print("Testing local backend...")
    url = "http://localhost:8000/api/v1/translate-sentence"
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
        with urllib.request.urlopen(req, timeout=45) as response:
            res = json.loads(response.read().decode('utf-8'))
            print("Status code:", response.status)
            with open("local_res.json", "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            print("Response written to local_res.json successfully!")
    except Exception as e:
        print("Failed to call local backend:", e)

if __name__ == "__main__":
    test_local_backend()
