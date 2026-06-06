#!/usr/bin/env python3
"""
Pasco/Hernando Combined Traffic Feed — TomTom + FHP
Pulls TomTom real-time incidents (jams, delays, closures) AND the FHP CadView
crash feed, merges them by road, and writes data/traffic.json for the website.
REPORT MODE: also prints everything so we can confirm accuracy before going live.
"""
import os, sys, json, re, datetime, urllib.request, urllib.parse

TOMTOM_KEY = os.environ.get("TOMTOM_KEY", "").strip()

# Bounding box over Pasco + Hernando (west,south,east,north)
BBOX = "-82.80,28.15,-82.05,28.85"

MONITORED = {
    "US 19": ["us-19","us 19","u.s. 19","highway 19"],
    "SR 54": ["sr-54","sr 54","state road 54"],
    "SR 52": ["sr-52","sr 52","state road 52"],
    "Little Road": ["little rd","little road"],
    "Ridge Road": ["ridge rd","ridge road"],
    "Suncoast Parkway": ["suncoast","veterans expressway","sr-589","sr 589"],
    "I-75": ["i-75","i 75","interstate 75"],
    "County Line Road": ["county line"],
    "Cortez Boulevard": ["cortez","sr-50","sr 50"],
    "Commercial Way": ["commercial way"],
    "Mariner Boulevard": ["mariner"],
}

def match_roads(text):
    t = (" " + (text or "").lower() + " ").replace("/", " ")
    hits = []
    for road, keys in MONITORED.items():
        for k in keys:
            if k in t:
                hits.append(road); break
    return hits

# ---------- TomTom ----------
ICON = {0:"Unknown",1:"Accident",2:"Fog",3:"Dangerous Conditions",4:"Rain",5:"Ice",
        6:"Traffic Jam",7:"Lane Closed",8:"Road Closed",9:"Road Works",10:"Wind",11:"Flooding",14:"Broken Down Vehicle"}
MAG = {0:"unknown",1:"minor",2:"moderate",3:"major",4:"closure"}

def fetch_tomtom():
    if not TOMTOM_KEY:
        print("WARN: no TomTom key"); return []
    fields = "{incidents{type,geometry{type,coordinates},properties{iconCategory,magnitudeOfDelay,events{description},startTime,endTime,from,to,length,delay,roadNumbers}}}"
    params = {"key":TOMTOM_KEY,"bbox":BBOX,"fields":fields,"language":"en-US","timeValidityFilter":"present"}
    url = "https://api.tomtom.com/traffic/services/5/incidentDetails?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent":"PascoCountyNews-Traffic/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8","replace"))
    except Exception as e:
        print("TomTom fetch error:", e); return []
    out = []
    for inc in data.get("incidents", []):
        p = inc.get("properties", {})
        events = p.get("events", [])
        desc = "; ".join([e.get("description","") for e in events]) if events else ""
        frm, to = p.get("from","") or "", p.get("to","") or ""
        roadnums = " ".join(p.get("roadNumbers",[]) or [])
        roads = match_roads(" ".join([desc, frm, to, roadnums]))
        coords = None
        g = inc.get("geometry", {})
        if g.get("type")=="LineString" and g.get("coordinates"):
            mid = g["coordinates"][len(g["coordinates"])//2]
            coords = [mid[1], mid[0]]  # lat,lon
        elif g.get("type")=="Point" and g.get("coordinates"):
            coords = [g["coordinates"][1], g["coordinates"][0]]
        out.append({
            "source":"TomTom",
            "kind": ICON.get(p.get("iconCategory",0),"Incident"),
            "severity": MAG.get(p.get("magnitudeOfDelay",0),"unknown"),
            "desc": desc, "from": frm, "to": to, "roadnums": roadnums,
            "roads": roads,
            "delay_sec": p.get("delay"), "length_m": p.get("length"),
            "coords": coords,
        })
    return out

# ---------- FHP CadView ----------
def fetch_fhp():
    url = "https://trafficincidents.flhsmv.gov/SmartWebClient/CadView.aspx"
    req = urllib.request.Request(url, headers={"User-Agent":"PascoCountyNews-Traffic/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8","replace")
    except Exception as e:
        print("FHP fetch error:", e); return []
    out = []
    for rowm in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S|re.I):
        cells = [re.sub(r"<[^>]+>","",c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", rowm, re.S|re.I)]
        if len(cells) < 5: continue
        rowtext = " ".join(cells)
        # only Pasco or Hernando rows
        if not re.search(r"PASCO|HERNANDO", rowtext, re.I): continue
        # find county
        county = "PASCO" if re.search(r"PASCO", rowtext, re.I) else "HERNANDO"
        # find lat/lon by Florida ranges
        lat=lon=None
        for c in cells:
            try: f=float(c)
            except: continue
            if 27.0<=f<=29.5 and lat is None: lat=f
            elif -83.5<=f<=-81.5 and lon is None: lon=f
        # incident type is usually first cell, location among cells
        itype = cells[0] if cells else "Incident"
        location = ""
        for c in cells:
            if re.search(r"\d", c) and re.search(r"[A-Za-z]", c) and len(c) > len(location):
                location = c
        roads = match_roads(rowtext)
        out.append({
            "source":"FHP","kind":itype,"severity":"reported",
            "desc":itype,"location":location,"county":county,
            "roads":roads,"coords":[lat,lon] if (lat and lon) else None,
        })
    return out

def main():
    print("=== Pasco/Hernando Combined Traffic Feed (TomTom + FHP) ===")
    tomtom = fetch_tomtom()
    fhp = fetch_fhp()
    print(f"TomTom incidents in area: {len(tomtom)}")
    print(f"FHP incidents (Pasco/Hernando): {len(fhp)}")
    print("="*60)

    print("\n--- TomTom on monitored roads ---")
    tt_roads = [x for x in tomtom if x["roads"]]
    for x in tt_roads:
        d = f" | delay {x['delay_sec']}s" if x.get("delay_sec") else ""
        print(f"  [{x['kind']}/{x['severity']}] {', '.join(x['roads'])}: {x['from']} -> {x['to']} {x['desc']}{d}")
    print(f"  ({len(tt_roads)} on monitored roads, {len(tomtom)} total in area)")

    print("\n--- FHP crashes/incidents ---")
    for x in fhp:
        flag = (" >> "+", ".join(x["roads"])) if x["roads"] else ""
        print(f"  [{x['county']}] {x['kind']} @ {x.get('location','')}{flag}")

    # Cross-reference: roads where BOTH sources show activity
    print("\n--- OVERLAP (both FHP + TomTom on same road) ---")
    tt_road_set = set()
    for x in tt_roads:
        for r in x["roads"]: tt_road_set.add(r)
    fhp_road_set = set()
    for x in fhp:
        for r in x["roads"]: fhp_road_set.add(r)
    overlap = tt_road_set & fhp_road_set
    if overlap:
        for r in overlap: print(f"  ** {r}: confirmed crash + traffic impact **")
    else:
        print("  (none right now)")

    # Write merged JSON for the website to pull later
    payload = {
        "generated": datetime.datetime.utcnow().isoformat()+"Z",
        "tomtom": tomtom, "fhp": fhp,
        "monitored_roads_active": sorted(list(tt_road_set | fhp_road_set)),
        "overlap": sorted(list(overlap)),
    }
    os.makedirs("data", exist_ok=True)
    with open("data/traffic.json","w") as f:
        json.dump(payload, f, indent=2)
    print("\nWrote data/traffic.json")

if __name__ == "__main__":
    main()
