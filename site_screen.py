"""
Site Screen — franchise/commercial parcel feasibility pre-check
Powered by the Mireye API (site_selection preset)

HOW TO USE:
1. Paste your Mireye API token into TOKEN below (keep the quote marks).
2. Run this file. It will ask you for latitude and longitude.
3. It prints a report to the screen AND saves it as a file called
   "site_report.html" in the same folder, which you can double-click
   to open in your browser and look nice.

Don't share this .py file with your token still pasted in it —
treat the token like a password.
"""

import requests
import json
import webbrowser
import os

# ---- PASTE YOUR TOKEN BETWEEN THE QUOTES BELOW ----
TOKEN = "api-key-here"
# ----------------------------------------------------

BASE_URL = "https://api.mireye.com"


def call_mireye(lat, lng):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "content-type": "application/json"
    }

    fetch_resp = requests.post(
        f"{BASE_URL}/v1/fetch",
        headers=headers,
        json={"lat": lat, "lng": lng, "preset": "site_selection"},
        timeout=30
    )
    if not fetch_resp.ok:
        raise RuntimeError(f"/v1/fetch failed ({fetch_resp.status_code}): {fetch_resp.text}")
    fetch_data = fetch_resp.json()

    ask_resp = requests.post(
        f"{BASE_URL}/v1/ask",
        headers=headers,
        json={
            "lat": lat, "lng": lng,
            "question": (
                "Is this parcel physically and legally buildable for commercial "
                "retail development? Consider slope, flood/wetland status, "
                "protected area or critical habitat status, and road access. "
                "Be direct about any blockers."
            )
        },
        timeout=30
    )
    ask_data = ask_resp.json() if ask_resp.ok else None

    return fetch_data, ask_data


def score_fields(fields):
    def get(key):
        entry = fields.get(key)
        return entry.get("value") if entry else None

    flags = []
    worst = "ok"  # ok -> warn -> bad, only escalates

    def add(level, text):
        nonlocal worst
        flags.append((level, text))
        if level == "bad":
            worst = "bad"
        elif level == "warn" and worst != "bad":
            worst = "warn"

    if get("within_floodplain_polygon") is True:
        add("bad", "Sits within a FEMA-mapped floodplain — expect flood insurance requirements and possible construction restrictions.")
    elif get("within_floodplain_polygon") is False:
        add("ok", "Not within a mapped FEMA floodplain.")

    if get("intersects_wetland") is True:
        add("bad", f"Intersects a wetland ({get('wetland_type') or 'type unspecified'}) — federal permitting likely required before construction.")
    else:
        near_wetland = get("nearest_wetland_distance_m")
        if isinstance(near_wetland, (int, float)) and near_wetland < 100:
            add("warn", f"Nearest wetland is {round(near_wetland)}m away — close enough to warrant a buffer check.")
        else:
            add("ok", "No wetland intersection detected.")

    if get("intersects_protected_area") is True:
        add("bad", "Site intersects a protected area — development is likely restricted or prohibited.")
    if get("intersects_critical_habitat") is True:
        add("bad", f"Site intersects designated critical habitat ({get('critical_habitat_status') or 'status unspecified'}) — Endangered Species Act review likely required.")
    if get("intersects_conservation_easement") is True:
        add("bad", "Site is under a conservation easement — development rights are likely already restricted by deed.")
    if not any([get("intersects_protected_area"), get("intersects_critical_habitat"), get("intersects_conservation_easement")]):
        add("ok", "No protected area, critical habitat, or conservation easement conflicts detected.")

    slope = get("slope_degrees")
    if isinstance(slope, (int, float)):
        if slope > 15:
            add("bad", f"Slope is {slope:.1f}° — steep enough to significantly raise grading/foundation costs.")
        elif slope > 8:
            add("warn", f"Slope is {slope:.1f}° — moderate, worth a grading estimate before committing.")
        else:
            add("ok", f"Slope is {slope:.1f}° — flat enough for standard construction.")

    road_dist = get("nearest_major_road_distance_m")
    if isinstance(road_dist, (int, float)):
        if road_dist > 300:
            add("warn", f"Nearest major road is {round(road_dist)}m away — check visibility/access for a retail storefront.")
        else:
            add("ok", f"Nearest major road is {round(road_dist)}m away — good frontage/access likely.")

    area = get("parcel_area_m2")
    if isinstance(area, (int, float)):
        acres = area / 4047
        if area < 1500:
            add("warn", f"Parcel is ~{round(area)}m² ({acres:.2f} acres) — on the small side for a standalone pad site with drive-thru and parking.")
        else:
            add("ok", f"Parcel is ~{round(area)}m² ({acres:.2f} acres).")

    tx_dist = get("nearest_transmission_line_distance_m")
    if isinstance(tx_dist, (int, float)) and tx_dist > 5000:
        add("warn", f"Nearest transmission line is {tx_dist/1000:.1f}km away — confirm standard utility hookup is available, this is a rough proxy only.")

    return flags, worst


def print_report(flags, worst, ask_data, fields, lat, lng, label):
    verdict_text = {"ok": "CLEAR TO PROCEED", "warn": "NEEDS REVIEW", "bad": "LIKELY BLOCKED"}[worst]
    print("\n" + "=" * 60)
    print(f"SITE SCREEN — {label or f'{lat}, {lng}'}")
    print("=" * 60)
    print(f"\nVERDICT: {verdict_text}\n")
    for level, text in flags:
        prefix = {"ok": "[clear]", "warn": "[check]", "bad": "[BLOCK]"}[level]
        print(f"  {prefix} {text}")
    print("\n--- Plain-english read ---")
    if ask_data and ask_data.get("answer"):
        print(ask_data["answer"])
    else:
        print("(unavailable for this call)")
    print("=" * 60 + "\n")


def save_html_report(flags, worst, ask_data, fields, lat, lng, label, path="site_report.html"):
    verdict_text = {"ok": "CLEAR TO PROCEED", "warn": "NEEDS REVIEW", "bad": "LIKELY BLOCKED"}[worst]
    color = {"ok": "#3f7a53", "warn": "#b3791f", "bad": "#a3341f"}[worst]

    flag_html = ""
    colors = {"ok": "#3f7a53", "warn": "#b3791f", "bad": "#a3341f"}
    for level, text in flags:
        flag_html += f'<li><span style="font-weight:700;color:{colors[level]};">[{level.upper()}]</span> {text}</li>'

    field_rows = ""
    for key, obj in fields.items():
        if key in ("parcel_geometry_wkt", "parcel_boundary_geojson"):
            continue
        val = obj.get("value")
        unit = obj.get("unit") or ""
        source = obj.get("source") or "—"
        field_rows += f"<tr><td>{key}</td><td><b>{val} {unit}</b></td><td style='font-size:11px;opacity:.6'>{source}</td></tr>"

    answer_text = ask_data.get("answer") if ask_data else "(unavailable)"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Site Screen — {label or f'{lat},{lng}'}</title>
<style>
body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 40px auto; color: #0e1b12; }}
h1 {{ font-size: 22px; }}
.verdict {{ font-size: 20px; font-weight: 800; color: {color}; border: 3px solid {color}; display:inline-block; padding: 8px 16px; border-radius: 4px; }}
ul {{ list-style: none; padding: 0; }}
li {{ padding: 8px 0; border-bottom: 1px solid #eee; }}
.answer {{ background: #fbf9f2; border-left: 3px solid #1c3d2e; padding: 14px; margin-top: 10px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 10px; }}
td, th {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; }}
</style></head>
<body>
<h1>Site Screen — {label or f'{lat}, {lng}'}</h1>
<div class="verdict">{verdict_text}</div>
<ul>{flag_html}</ul>
<h3>Plain-english read</h3>
<div class="answer">{answer_text}</div>
<h3>Raw field data</h3>
<table><tr><th>Field</th><th>Value</th><th>Source</th></tr>{field_rows}</table>
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    if TOKEN == "api-key-here":
        print("Stop! Open this file in a text editor and paste your real Mireye token")
        print("into the TOKEN = \"...\" line near the top, then save and re-run.")
        return

    print("=== Mireye Site Screen ===")
    lat = float(input("Latitude (e.g. 33.6595): ").strip())
    lng = float(input("Longitude (e.g. -117.9988): ").strip())
    label = input("Site nickname (optional, press Enter to skip): ").strip()

    print("\nCalling Mireye...\n")
    fetch_data, ask_data = call_mireye(lat, lng)
    fields = fetch_data.get("fields", {})

    flags, worst = score_fields(fields)
    print_report(flags, worst, ask_data, fields, lat, lng, label)

    path = save_html_report(flags, worst, ask_data, fields, lat, lng, label)
    full_path = os.path.abspath(path)
    print(f"Saved a nicer-looking report to: {full_path}")

    try:
        webbrowser.open(f"file://{full_path}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
