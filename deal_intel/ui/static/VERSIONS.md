# Vendored static assets

| File | Package | Version | Source |
|---|---|---|---|
| `htmx.min.js` | htmx.org | 2.0.11 | https://unpkg.com/htmx.org@2.0.11/dist/htmx.min.js |

Checksums of the vendored `htmx.min.js`:

- sha384 (SRI): `sha384-2OatzQy1H+Zd/IIrjr1TcuDGqLXeHhbooAyJY1KdQMKnr4LZ22k31GBLdYKHmVjg`
- sha256: `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717`

Verified on 2026-09-26: the file is byte-identical to `package/dist/htmx.min.js` in the npm
tarball `htmx.org-2.0.11.tgz`, whose sha512 matched the registry's `dist.integrity`
(`sha512-Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA==`).

To check the vendored file:

```bash
openssl dgst -sha384 -binary deal_intel/ui/static/htmx.min.js | openssl base64 -A
shasum -a 256 deal_intel/ui/static/htmx.min.js
```
