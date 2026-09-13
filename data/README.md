# Data

`official-business-data.xlsx` is the competition's synthetic business dataset. The backend reads it without modifying it and stores normalized records in `runtime/empathy.db`.

The workbook contains 29 image-message paths but the referenced image files are not included. The MVP therefore records that an image was provided without pretending that its pixels were analyzed. A later multimodal extension can attach the real files and pass them to Qwen Omni.

The Markdown files in `knowledge/` are demo policies created for this prototype. They are not official L'Oreal policies and must be replaced or reviewed before any production use.

`knowledge/openbeautyfacts-lipstick.md` is a small public-data sample sourced from Open Beauty Facts. The database is licensed under ODbL 1.0. The source URL and license are kept in the document metadata; it is used only to demonstrate attributable retrieval and must not be treated as brand-authoritative product guidance.

`knowledge/loreal-*.md` (7 files, added 2026-09-13) are enrichment documents compiled from public web research (L'Oréal Paris official beauty magazine, 科普中国, government regulations such as 《网络购买商品七日无理由退货暂行办法》 and 《化妆品标签管理办法》, plus general industry consensus on retinol/sunscreen/PAO usage). They cover: retinol usage, Pro-Xylane ingredient background, pregnancy/lactation skincare consultation boundaries, makeup PAO shelf-life and storage, sunscreen usage, authenticity-verification and official-channel caution, and the statutory seven-day no-reason-return rule for cosmetics. Each document's `authority` field (1 or 2) reflects source certainty (1 = general public consensus / unverified official channel, 2 = brand/official-science-outlet or national-regulation sourced); none is set to 3, which this project reserves for the pre-existing, code-referenced, mandatory internal SOPs. None of these were retrieved from L'Oréal's internal/confidential systems; where an official first-hand product page could not be fetched (JS-rendered sites), the document explicitly says so and instructs customer service to defer to the actual product packaging/detail page. They must still be reviewed before production use.
