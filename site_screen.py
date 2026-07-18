"""
Site Screen v2 — franchise/commercial parcel feasibility pre-check
Powered by the Mireye API (site_selection preset + explicit extra fields)

WHAT'S NEW IN V2:
- Weighted 0-100 score instead of a single worst-flag verdict, so one soft
  issue can't override several strong signals.
- Three priority profiles (balanced / risk-averse / growth) you choose at
  the start — weights the same data differently depending on who's asking.
- New fields: opportunity zone status, wildfire/hail frequency, housing
  density (informational), hospital distance.
- Zoning uses a deny-list (flag clearly non-commercial zones) instead of an
  allow-list (guess every possible commercial code name) — generalizes
  better across counties with different zoning-code naming.
- Batch mode: run a whole CSV of candidate lots and get a ranked table.

HOW TO USE:
1. Paste your Mireye API token into TOKEN below.
2. Run this file: python site_screen.py
3. Choose single-site or batch mode, and a priority profile.
4. For batch mode, point it at a CSV with columns: lat,lng,label
   (label is optional — leave the column blank or omit rows' 3rd value).

Don't share this .py file with your token still pasted in it.
"""

import requests
import json
import webbrowser
import os
import csv

# ---- PASTE YOUR TOKEN BETWEEN THE QUOTES BELOW ----
TOKEN = "PASTE_YOUR_MIREYE_TOKEN_HERE"
# ----------------------------------------------------

BASE_URL = "https://api.mireye.com"

EXTRA_FIELDS = [
    "parcel_zoning",
    "parcel_owner",
    "in_opportunity_zone",
    "wildfire_annual_frequency",
    "hail_annual_frequency",
    "housing_units_density_per_km2",
    "nearest_hospital_distance_m",
]

WEIGHTS = {
    "balanced": {
        "zoning_bad": -40, "zoning_missing": -15,
        "floodplain": -30, "wetland": -35,
        "protected_area": -35, "critical_habitat": -35, "conservation_easement": -35,
        "slope_steep": -15, "slope_moderate": -5,
        "road_far": -15,
        "poi_sparse": -10,
        "near_school": -5,
        "hazard_high": -10, "hazard_medium": -5,
        "opportunity_zone": 10,
    },
    "risk_averse": {
        "zoning_bad": -50, "zoning_missing": -20,
        "floodplain": -45, "wetland": -50,
        "protected_area": -50, "critical_habitat": -50, "conservation_easement": -50,
        "slope_steep": -20, "slope_moderate": -8,
        "road_far": -10,
        "poi_sparse": -5,
        "near_school": -15,
        "hazard_high": -20, "hazard_medium": -10,
        "opportunity_zone": 5,
    },
    "growth": {
        "zoning_bad": -35, "zoning_missing": -10,
        "floodplain": -20, "wetland": -25,
        "protected_area": -25, "critical_habitat": -25, "conservation_easement": -25,
        "slope_steep": -10, "slope_moderate": -3,
        "road_far": -25,
        "poi_sparse": -20,
        "near_school": -5,
        "hazard_high": -5, "hazard_medium": -3,
        "opportunity_zone": 15,
    },
}

NONCOMMERCIAL_ZONE_HINTS = [
    "residential", "single family", "single-family", "r-1", "r-2", "r-3",
    "r1", "r2", "r3", "rural residential", "agricultural", "ag-", "farm",
    "conservation", "open space",
]


def call_mireye(lat, lng):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "content-type": "application/json"
    }

    fetch_resp = requests.post(
        f"{BASE_URL}/v1/fetch",
        headers=headers,
        json={"lat": lat, "lng": lng, "preset": "site_selection", "fields": EXTRA_FIELDS},
        timeout=30
    )
    if not fetch_resp.ok:
        raise RuntimeError(f"/v1/fetch failed ({fetch_resp.status_code}): {fetch_resp.text}")
    fetch_data = fetch_resp.json()

    ask_data = None
    try:
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
            timeout=60
        )
        if ask_resp.ok:
            ask_data = ask_resp.json()
    except requests.exceptions.RequestException as e:
        print(f"(Note: /v1/ask timed out or failed — {e}. Continuing with /v1/fetch data only.)")

    return fetch_data, ask_data


def score_site(fields, partial_failures, profile):
    w = WEIGHTS[profile]
    failure_by_field = {pf.get("field"): pf for pf in (partial_failures or [])}

    def get(key):
        entry = fields.get(key)
        return entry.get("value") if entry else None

    score = 100
    reasons = []

    def apply(delta, text):
        nonlocal score
        score += delta
        reasons.append((delta, text))

    if get("within_floodplain_polygon") is True:
        apply(w["floodplain"], "Sits within a FEMA-mapped floodplain.")
    elif get("within_floodplain_polygon") is False:
        reasons.append((0, "Not within a mapped FEMA floodplain."))

    if get("intersects_wetland") is True:
        apply(w["wetland"], f"Intersects a wetland ({get('wetland_type') or 'type unspecified'}).")
    else:
        reasons.append((0, "No wetland intersection detected."))

    if get("intersects_protected_area") is True:
        apply(w["protected_area"], "Intersects a protected area.")
    if get("intersects_critical_habitat") is True:
        apply(w["critical_habitat"], f"Intersects designated critical habitat ({get('critical_habitat_status') or 'status unspecified'}).")
    if get("intersects_conservation_easement") is True:
        apply(w["conservation_easement"], "Under a conservation easement.")
    if not any([get("intersects_protected_area"), get("intersects_critical_habitat"), get("intersects_conservation_easement")]):
        reasons.append((0, "No protected area, critical habitat, or conservation easement conflicts."))

    slope = get("slope_degrees")
    if isinstance(slope, (int, float)):
        if slope > 15:
            apply(w["slope_steep"], f"Slope is {slope:.1f}° — steep, raises grading/foundation cost.")
        elif slope > 8:
            apply(w["slope_moderate"], f"Slope is {slope:.1f}° — moderate, worth a grading estimate.")
        else:
            reasons.append((0, f"Slope is {slope:.1f}° — flat, standard construction."))

    road_dist = get("nearest_major_road_distance_m")
    if isinstance(road_dist, (int, float)):
        if road_dist > 300:
            apply(w["road_far"], f"Nearest major road is {round(road_dist)}m away — check visibility/access.")
        else:
            reasons.append((0, f"Nearest major road is {round(road_dist)}m away — good frontage/access."))

    area = get("parcel_area_m2")
    if isinstance(area, (int, float)):
        acres = area / 4047
        reasons.append((0, f"Parcel is ~{round(area)}m² ({acres:.2f} acres)."))

    zoning = get("parcel_zoning")
    if zoning is None:
        pf = failure_by_field.get("parcel_zoning")
        detail = f" (Mireye: \"{pf.get('error')}\")" if pf else ""
        apply(w["zoning_missing"], f"Zoning was not returned{detail} — confirm manually with the county.")
    else:
        zoning_lower = str(zoning).lower()
        if any(hint in zoning_lower for hint in NONCOMMERCIAL_ZONE_HINTS):
            apply(w["zoning_bad"], f"Zoned '{zoning}' — appears non-commercial. Verify; a rezone/variance may be required.")
        else:
            reasons.append((0, f"Zoned '{zoning}' — no obvious conflict with commercial use (confirm locally, zoning codes vary by county)."))

    poi_count = get("poi_count_1km")
    if isinstance(poi_count, (int, float)):
        if poi_count <= 5:
            apply(w["poi_sparse"], f"Only {int(poi_count)} mapped businesses within 1km — sparse area, confirm this matches the intended trade area.")
        else:
            reasons.append((0, f"{int(poi_count)} other mapped businesses within 1km — active commercial area (density proxy only, not real foot-traffic data)."))

    school_dist = get("nearest_school_distance_m")
    if isinstance(school_dist, (int, float)) and school_dist < 150:
        apply(w["near_school"], f"A school is {round(school_dist)}m away — check local ordinances (signage, alcohol permits, etc).")

    wildfire = get("wildfire_annual_frequency")
    if isinstance(wildfire, (int, float)):
        if wildfire > 0.05:
            apply(w["hazard_high"], f"Wildfire annual frequency ({wildfire:.3f}) is elevated for this tract.")
        elif wildfire > 0.01:
            apply(w["hazard_medium"], f"Wildfire annual frequency ({wildfire:.3f}) is moderate for this tract.")

    hail = get("hail_annual_frequency")
    if isinstance(hail, (int, float)):
        if hail > 0.3:
            apply(w["hazard_high"], f"Hail annual frequency ({hail:.3f}) is elevated — relevant for signage/roofing insurance cost.")
        elif hail > 0.1:
            apply(w["hazard_medium"], f"Hail annual frequency ({hail:.3f}) is moderate.")

    if get("in_opportunity_zone") is True:
        apply(w["opportunity_zone"], "Inside a designated Opportunity Zone — potential capital-gains tax incentive for investors.")

    housing_density = get("housing_units_density_per_km2")
    if isinstance(housing_density, (int, float)):
        reasons.append((0, f"Housing density nearby: ~{housing_density:.0f} units/km² (rough demographic proxy, not real population/traffic data)."))

    hospital_dist = get("nearest_hospital_distance_m")
    if isinstance(hospital_dist, (int, float)):
        reasons.append((0, f"Nearest hospital: {hospital_dist/1000:.1f}km away (informational, relevant to insurability)."))

    score = min(100, score)
    return score, reasons


def verdict_for_score(score):
    if score >= 80:
        return "CLEAR TO PROCEED"
    elif score >= 50:
        return "NEEDS REVIEW"
    else:
        return "LIKELY BLOCKED"


def print_single_report(label, lat, lng, score, reasons, ask_data):
    verdict = verdict_for_score(score)
    print("\n" + "=" * 60)
    print(f"SITE SCREEN — {label or f'{lat}, {lng}'}")
    print("=" * 60)
    print(f"\nSCORE: {score}/100 — {verdict}\n")
    for delta, text in reasons:
        tag = f"({delta:+d})" if delta != 0 else "     "
        print(f"  {tag} {text}")
    print("\n--- Plain-english read ---")
    if ask_data and ask_data.get("answer"):
        print(ask_data["answer"])
    else:
        print("(unavailable for this call)")
    print("=" * 60 + "\n")


def save_single_html(label, lat, lng, score, reasons, ask_data, fields, path="site_report.html"):
    verdict = verdict_for_score(score)
    color = "#3f7a53" if score >= 80 else "#b3791f" if score >= 50 else "#a3341f"

    reason_html = ""
    for delta, text in reasons:
        tag_color = "#3f7a53" if delta > 0 else "#a3341f" if delta < 0 else "#666"
        tag_text = f"{delta:+d}" if delta != 0 else "info"
        reason_html += f'<li><span style="font-weight:700;color:{tag_color};">[{tag_text}]</span> {text}</li>'

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
<div class="verdict">{score}/100 — {verdict}</div>
<ul>{reason_html}</ul>
<h3>Plain-english read</h3>
<div class="answer">{answer_text}</div>
<h3>Raw field data</h3>
<table><tr><th>Field</th><th>Value</th><th>Source</th></tr>{field_rows}</table>
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def save_batch_html(results, path="site_report_batch.html"):
    rows = ""
    for r in sorted(results, key=lambda x: -x["score"]):
        color = "#3f7a53" if r["score"] >= 80 else "#b3791f" if r["score"] >= 50 else "#a3341f"
        rows += (
            f"<tr><td>{r['label']}</td><td>{r['lat']}, {r['lng']}</td>"
            f"<td style='color:{color};font-weight:800;'>{r['score']}/100</td>"
            f"<td>{verdict_for_score(r['score'])}</td>"
            f"<td style='font-size:11px;'>{r['top_issue']}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Site Screen — Batch Ranking</title>
<style>
body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; color: #0e1b12; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
td, th {{ text-align: left; padding: 8px; border-bottom: 1px solid #eee; }}
th {{ text-transform: uppercase; font-size: 11px; opacity: 0.6; }}
</style></head>
<body>
<h1>Site Screen — Batch Ranking</h1>
<table><tr><th>Label</th><th>Coordinates</th><th>Score</th><th>Verdict</th><th>Top issue</th></tr>{rows}</table>
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def choose_profile():
    print("\nWhich priority profile?")
    print("  1) Balanced")
    print("  2) Risk-averse (weighs hazard/legal risk heavily)")
    print("  3) Growth/speed (weighs access/commercial density heavily)")
    choice = input("Enter 1, 2, or 3: ").strip()
    return {"1": "balanced", "2": "risk_averse", "3": "growth"}.get(choice, "balanced")


def run_single():
    profile = choose_profile()
    lat = float(input("Latitude: ").strip())
    lng = float(input("Longitude: ").strip())
    label = input("Site nickname (optional): ").strip()

    print("\nCalling Mireye...\n")
    fetch_data, ask_data = call_mireye(lat, lng)
    fields = fetch_data.get("fields", {})
    partial_failures = fetch_data.get("partial_failures", [])

    score, reasons = score_site(fields, partial_failures, profile)
    print_single_report(label, lat, lng, score, reasons, ask_data)

    path = save_single_html(label, lat, lng, score, reasons, ask_data, fields)
    full_path = os.path.abspath(path)
    print(f"Saved report to: {full_path}")
    try:
        webbrowser.open(f"file://{full_path}")
    except Exception:
        pass


def run_batch():
    profile = choose_profile()
    csv_path = input("Path to CSV file (columns: lat,lng,label): ").strip().strip('"')

    if not os.path.exists(csv_path):
        print(f"Could not find file: {csv_path}")
        return

    results = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if rows and not rows[0][0].replace(".", "").replace("-", "").isdigit():
        rows = rows[1:]

    for i, row in enumerate(rows):
        if len(row) < 2:
            continue
        lat, lng = float(row[0]), float(row[1])
        label = row[2].strip() if len(row) > 2 and row[2].strip() else f"site_{i+1}"

        print(f"Calling Mireye for {label} ({lat}, {lng})...")
        try:
            fetch_data, ask_data = call_mireye(lat, lng)
        except Exception as e:
            print(f"  Failed: {e}")
            continue

        fields = fetch_data.get("fields", {})
        partial_failures = fetch_data.get("partial_failures", [])
        score, reasons = score_site(fields, partial_failures, profile)

        negative_reasons = [r for r in reasons if r[0] < 0]
        top_issue = min(negative_reasons, key=lambda r: r[0])[1] if negative_reasons else "No major issues found."

        results.append({"label": label, "lat": lat, "lng": lng, "score": score, "top_issue": top_issue})

    print("\n" + "=" * 60)
    print("BATCH RESULTS (ranked best to worst)")
    print("=" * 60)
    for r in sorted(results, key=lambda x: -x["score"]):
        print(f"  {r['score']:>3}/100  {verdict_for_score(r['score']):<18} {r['label']}  ({r['lat']}, {r['lng']})")
        print(f"           top issue: {r['top_issue']}")
    print("=" * 60 + "\n")

    path = save_batch_html(results)
    full_path = os.path.abspath(path)
    print(f"Saved ranked report to: {full_path}")
    try:
        webbrowser.open(f"file://{full_path}")
    except Exception:
        pass


def main():
    if TOKEN == "PASTE_YOUR_MIREYE_TOKEN_HERE":
        print("Stop! Paste your real Mireye token into the TOKEN = \"...\" line near the top, then save and re-run.")
        return

    print("=== Mireye Site Screen v2 ===")
    mode = input("Single site or batch CSV? (single/batch): ").strip().lower()

    if mode.startswith("b"):
        run_batch()
    else:
        run_single()


if __name__ == "__main__":
    main()
