import json
import os
from datetime import datetime, timezone
import requests
from curl_cffi import requests as cffi_requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()


def send_alert(title, message, click_url=""):
    if not NTFY_TOPIC:
        return
    headers = {"Title": title, "Priority": "high"}
    if click_url:
        headers["Click"] = click_url
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
    except Exception as e:
        print(f"Notification error: {e}")


def check_bestbuy_ca(sku):
    url = f"https://www.bestbuy.ca/api/v2/json/product/{sku}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    price = data.get("salePrice") or data.get("regularPrice")
    avail = data.get("availability", {})
    online_status = avail.get("onlineAvailability", "")
    in_stock = online_status in ("InStock", "BackOrder") or data.get("isOnlineAvailable", False)
    product_url = f"https://www.bestbuy.ca/en-ca/product/{sku}"
    return price, in_stock, product_url


def check_fragrancebuy_ca(product_url):
    clean_url = product_url.split("?")[0].rstrip("/")
    json_url = clean_url if clean_url.endswith(".json") else f"{clean_url}.json"
    resp = requests.get(json_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    variants = resp.json().get("product", {}).get("variants", [])

    if not variants:
        return None, False, clean_url

    in_stock_variants = [v for v in variants if v.get("available")]
    chosen = in_stock_variants[0] if in_stock_variants else variants[0]
    price = float(chosen["price"])
    in_stock = bool(in_stock_variants)
    return price, in_stock, clean_url


def check_uniqlo_ca(product_id):
    base_url = f"https://www.uniqlo.com/ca/api/commerce/v5/en/products/{product_id}/price-groups/00"
    product_url = f"https://www.uniqlo.com/ca/en/products/{product_id}/00"
    uniqlo_headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": product_url,
        "x-fr-clientid": "uq.ca.web-spa",
    }
    resp = cffi_requests.get(
        f"{base_url}/details",
        headers=uniqlo_headers,
        impersonate="chrome",
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json().get("result", {})

    prices = result.get("prices", {})
    if not prices and result.get("l2s"):
        prices = result["l2s"][0].get("prices", {})
    promo = prices.get("promo") or {}
    base = prices.get("base") or {}
    raw_price = promo.get("value") if promo.get("value") is not None else base.get("value")
    price = float(raw_price) if raw_price is not None else None

    l2s = result.get("l2s", [])
    in_stock = any(
        sku.get("stock", {}).get("statusCode") in ("IN_STOCK", "LOW_STOCK")
        for sku in l2s
    ) if l2s else (price is not None)

    return price, in_stock, product_url



def main():
    with open("config.json", "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    previous = {}
    if os.path.exists("prices.json"):
        with open("prices.json", "r", encoding="utf-8") as f:
            old_data = json.load(f)
            previous = {item["id"]: item for item in old_data.get("items", [])}

    now_iso = datetime.now(timezone.utc).isoformat()
    results = []

    for item in watchlist:
        item_id = item["id"]
        retailer = item["retailer"]
        prev = previous.get(item_id, {})
        history = prev.get("history", [])

        price, in_stock, product_url = prev.get("currentPrice"), prev.get("inStock", False), item.get("url", "")
        status = "ok"

        try:
            if retailer == "bestbuy_ca":
                price, in_stock, product_url = check_bestbuy_ca(item["sku"])
            elif retailer == "fragrancebuy_ca":
                price, in_stock, product_url = check_fragrancebuy_ca(item["url"])
            elif retailer == "uniqlo_ca":
                price, in_stock, product_url = check_uniqlo_ca(item["productId"])
        except Exception as e:
            print(f"Failed checking {item_id}: {e}")
            if retailer == "uniqlo_ca" and not product_url:
                product_url = f"https://www.uniqlo.com/ca/en/products/{item['productId']}/00"
            status = f"error: {e}"


        if price is not None:
            if not history or history[-1]["price"] != price:
                history.append({"date": now_iso, "price": price})
            history = history[-60:]  # Keep last 60 price changes

        was_in_stock = prev.get("inStock", False)
        was_at_target = (
            prev.get("currentPrice") is not None
            and prev.get("currentPrice") <= item["targetPrice"]
        )
        now_at_target = price is not None and price <= item["targetPrice"]

        # Alert on new restock or new target price hit
        if in_stock and not was_in_stock:
            send_alert(
                f"Back in Stock: {item['name']}",
                f"Now ${price:.2f} CAD (Target: ${item['targetPrice']:.2f})",
                product_url,
            )
        elif in_stock and now_at_target and not was_at_target:
            send_alert(
                f"Price Drop: {item['name']}",
                f"Dropped to ${price:.2f} CAD (Target: ${item['targetPrice']:.2f})",
                product_url,
            )

        results.append({
            "id": item_id,
            "name": item["name"],
            "retailer": retailer,
            "url": product_url,
            "currentPrice": price,
            "targetPrice": item["targetPrice"],
            "inStock": in_stock,
            "status": status,
            "lastChecked": now_iso,
            "history": history,
        })

    output = {"updatedAt": now_iso, "items": results}
    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)


if __name__ == "__main__":
    main()
