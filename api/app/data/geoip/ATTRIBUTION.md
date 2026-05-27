# IP-to-Country database attribution

`dbip-country-lite.mmdb` is the **DB-IP IP-to-Country Lite** database by
DB-IP (<https://db-ip.com>), distributed under the
[Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/).

Bundled snapshot: `dbip-country-lite-2026-05.mmdb` (downloaded 2026-05-27).

The database is updated monthly by DB-IP. To refresh:

```sh
curl -fsSL "https://download.db-ip.com/free/dbip-country-lite-$(date +%Y-%m).mmdb.gz" \
  | gunzip > api/app/data/geoip/dbip-country-lite.mmdb
```

Operators can also override the path with `GEOIP_DB_PATH=/path/to/your.mmdb`
to use a licensed MaxMind GeoLite2-Country build instead (same MMDB format).
