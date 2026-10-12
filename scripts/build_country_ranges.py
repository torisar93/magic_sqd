"""База стран адресов для правила доступа по региону (server/access_policy.py) — только для старых версий программы,
которые не присылают признаки системы. Берёт офлайн-базу DB-IP Lite (mmdb, бесплатная, CC BY 4.0:
https://db-ip.com/db/lite.php) и оставляет диапазоны адресов нужных стран — сервер ищет по ним двоичным поиском без
сторонних библиотек.

    python scripts/build_country_ranges.py dbip-city-lite-2026-10.mmdb DZ SA > country_ranges.json

Потом файл — на сервер в /opt/magicsqd/state/country_ranges.json (владелец magicsqd). Нужен пакет maxminddb
(pip install maxminddb) — только здесь, на сервере он не нужен."""
from __future__ import annotations

import json
import sys

import maxminddb


def build(db_path: str, countries: set[str]) -> dict:
    tables = {4: [], 6: []}
    with maxminddb.open_database(db_path) as reader:
        for network, record in reader:
            iso = ((record or {}).get("country") or {}).get("iso_code")
            if iso not in countries:
                continue
            start, end = int(network.network_address), int(network.broadcast_address)
            if network.version == 6 and network.network_address.ipv4_mapped:
                continue  # те же адреса уже есть в v4
            table = tables[network.version]
            if table and table[-1][2] == iso and table[-1][1] + 1 == start:
                table[-1][1] = end  # соседние сети одной страны — одним диапазоном
            else:
                table.append([start, end, iso])
    for table in tables.values():
        table.sort()
    return {"source": "DB-IP Lite (CC BY 4.0, https://db-ip.com)", "countries": sorted(countries),
            "v4": tables[4], "v6": tables[6]}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("python scripts/build_country_ranges.py база.mmdb КОД [КОД…]")
    data = build(sys.argv[1], {code.upper() for code in sys.argv[2:]})
    print(f"v4: {len(data['v4'])} диапазонов, v6: {len(data['v6'])}", file=sys.stderr)
    json.dump(data, sys.stdout, separators=(",", ":"))
