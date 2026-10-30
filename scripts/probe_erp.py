import os, json, time, requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.environ["ERP_BASE_URL"]
H = {"X-API-Key": os.environ["ERP_API_KEY"]}


def get(path, **params):
    t = time.time()
    try:
        r = requests.get(BASE + path, headers=H, params=params, timeout=15)
        print(f"{path} {params} -> {r.status_code} in {time.time()-t:.2f}s")
        return r
    except Exception as e:
        print(f"{path} {params} -> FAILED: {e}")
        return None


c = get("/v1/customers")
if c is not None and c.ok:
    j = c.json()
    print("customers total:", j.get("total_records"))
    print(json.dumps(j["data"][:2], indent=1))
    json.dump(j, open("scripts/raw_customers.json", "w"), indent=1)

o = get("/v1/orders", page=1, page_size=50)
if o is not None and o.ok:
    j = o.json()
    print({k: v for k, v in j.items() if k != "data"})
    print(json.dumps(j["data"][:2], indent=1))
    json.dump(j, open("scripts/raw_orders_p1.json", "w"), indent=1)
elif o is not None:
    print(o.status_code, o.text[:300])