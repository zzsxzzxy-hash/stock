import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputJson = process.argv[2];
const outputDir = process.argv[3];
if (!inputJson || !outputDir) {
  throw new Error("Usage: node build_statement_report.mjs analysis.json output_dir");
}

const data = JSON.parse(await fs.readFile(inputJson, "utf8"));

const pct = (v) => (v === null || v === undefined || Number.isNaN(v) ? null : Number(v));
const num = (v) => (v === null || v === undefined || Number.isNaN(v) ? null : Number(v));
const dt = (v) => (v || null);

function addSheet(workbook, name, rows, options = {}) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const range = sheet.getRangeByIndexes(0, 0, rows.length, rows[0]?.length || 1);
  range.values = rows;
  sheet.freezePanes.freezeRows(options.freezeRows ?? 1);
  if (options.freezeCols) sheet.freezePanes.freezeColumns(options.freezeCols);

  const used = sheet.getUsedRange();
  used.format.font.name = "Arial";
  used.format.font.size = 10;
  used.format.wrapText = false;

  const headerRow = sheet.getRangeByIndexes(options.headerRow ?? 0, 0, 1, rows[0]?.length || 1);
  headerRow.format.fill.color = "#1F4E78";
  headerRow.format.font.color = "#FFFFFF";
  headerRow.format.font.bold = true;
  headerRow.format.rowHeight = 24;
  headerRow.format.horizontalAlignment = "center";

  used.format.borders = { preset: "insideHorizontal", style: "thin", color: "#E6EAF0" };
  used.format.autofitColumns();
  used.format.autofitRows();
  return sheet;
}

function setWidths(sheet, widths) {
  for (const [col, width] of Object.entries(widths)) {
    sheet.getRange(`${col}:${col}`).format.columnWidth = width;
  }
}

function styleNumericColumns(sheet, rowCount, cols) {
  for (const [col, format] of Object.entries(cols)) {
    sheet.getRange(`${col}2:${col}${rowCount}`).setNumberFormat(format);
    sheet.getRange(`${col}2:${col}${rowCount}`).format.horizontalAlignment = "right";
  }
}

function stylePctBySign(sheet, rangeAddress) {
  // Keep percentages readable across spreadsheet renderers; avoid heatmap fills here.
}

await fs.mkdir(outputDir, { recursive: true });

const workbook = Workbook.create();

const cover = workbook.worksheets.add("说明");
cover.showGridLines = false;
cover.getRange("A1:H1").merge();
cover.getRange("A1").values = [["股票交割单收益对比报告"]];
cover.getRange("A1").format.font.size = 18;
cover.getRange("A1").format.font.bold = true;
cover.getRange("A1").format.font.color = "#1F4E78";
cover.getRange("A3:B10").values = [
  ["源文件", data.metadata.source_file],
  ["统计日期", data.metadata.as_of],
  ["买入笔数", data.metadata.buy_rows],
  ["交易流水数", data.metadata.trade_rows],
  ["行情行数", data.metadata.price_rows],
  ["实际收益口径", "本月买入记录 FIFO 匹配后续卖出；卖出净收入使用交割单“发生金额”。"],
  ["假设持有口径", "每笔买入从买入日持有到 2026-06-30；今日收盘收益按今日收盘价与含费买入成本计算；最高收益按日线最高价/成交均价，回撤按买入后高点到后续低点。"],
  ["注意", "月初已有持仓卖出无法确认原始成本，已单独列在“未匹配卖出”。"],
];
cover.getRange("A3:A10").format.font.bold = true;
cover.getRange("A3:A10").format.fill.color = "#D9EAF7";
cover.getRange("A3:B10").format.borders = { preset: "all", style: "thin", color: "#B7C9D6" };
cover.getRange("A:B").format.autofitColumns();
cover.getRange("B3:B10").format.wrapText = true;
cover.getRange("B:B").format.columnWidth = 90;

const summaryHeaders = [
  "证券代码", "证券名称", "买入笔数", "首次买入", "最后买入", "买入数量", "买入总成本",
  "加权买入价", "今日收盘", "今日收盘收益(%)", "今日收盘收益(金额)", "假设最高收益", "假设最大回撤", "买后最低收益",
  "实际已匹配成本", "实际收益", "实际收益率", "实际卖出数量", "仍未卖出数量", "行情缺失数"
];
const summaryRows = data.summary.map((r) => [
  r.code, r.name, r.buy_count, dt(r.first_buy_date), dt(r.last_buy_date), num(r.total_buy_qty),
  num(r.total_buy_cost), num(r.weighted_buy_price), num(r.latest_close),
  r.latest_close == null || r.total_buy_cost == null ? null : pct((r.latest_close * r.total_buy_qty - r.total_buy_cost) / r.total_buy_cost),
  r.latest_close == null || r.total_buy_cost == null ? null : num(r.latest_close * r.total_buy_qty - r.total_buy_cost),
  pct(r.best_max_gain_pct),
  pct(r.worst_max_drawdown_pct), pct(r.worst_lowest_return_pct), num(r.actual_matched_cost),
  num(r.actual_pnl), pct(r.actual_return_pct), num(r.actual_sold_qty), num(r.actual_unsold_qty),
  num(r.price_note_count),
]);
const summary = addSheet(workbook, "股票汇总", [summaryHeaders, ...summaryRows], { freezeRows: 1, freezeCols: 2 });
styleNumericColumns(summary, summaryRows.length + 1, {
  D: "yyyy-mm-dd", E: "yyyy-mm-dd", F: "#,##0", G: "#,##0.00", H: "0.000", I: "0.000",
  J: "0.00%", K: "#,##0.00", L: "0.00%", M: "0.00%", N: "0.00%", O: "#,##0.00", P: "#,##0.00", Q: "0.00%",
  R: "#,##0", S: "#,##0", T: "#,##0",
});
stylePctBySign(summary, `J2:J${summaryRows.length + 1}`);
stylePctBySign(summary, `L2:N${summaryRows.length + 1}`);
stylePctBySign(summary, `Q2:Q${summaryRows.length + 1}`);
setWidths(summary, { A: 11, B: 14, D: 12, E: 12, J: 15, K: 18, L: 13, M: 13, N: 13, P: 13, Q: 12 });

const detailHeaders = [
  "买入ID", "买入日期", "证券代码", "证券名称", "股东账号", "买入数量", "成交均价",
  "买入费用", "含费总成本", "含费成本价", "实际卖出数量", "实际未卖出数量", "实际卖出日期",
  "实际卖出净收入", "实际匹配成本", "实际收益", "实际收益率", "行情起始", "行情截至",
  "今日收盘", "今日收盘收益(%)", "今日收盘收益(金额)", "最高价", "最高价日期", "假设最高收益", "最低价", "最低价日期",
  "买后最低收益", "假设最大回撤", "持有至今日收益", "行情天数", "备注"
];
const detailRows = data.detail.map((r) => [
  r.trade_id, dt(r.buy_date), r.code, r.name, r.account, num(r.buy_qty), num(r.buy_price),
  num(r.buy_fees), num(r.buy_total_cost), num(r.buy_cost_price), num(r.actual_sold_qty),
  num(r.actual_unsold_qty), r.actual_sell_dates, num(r.actual_sell_proceeds),
  num(r.actual_cost_matched), num(r.actual_pnl), pct(r.actual_return_pct), dt(r.price_start),
  dt(r.price_end), num(r.latest_close),
  r.latest_close == null || r.buy_total_cost == null ? null : pct((r.latest_close * r.buy_qty - r.buy_total_cost) / r.buy_total_cost),
  r.latest_close == null || r.buy_total_cost == null ? null : num(r.latest_close * r.buy_qty - r.buy_total_cost),
  num(r.max_high), dt(r.max_high_date), pct(r.max_gain_pct),
  num(r.min_low), dt(r.min_low_date), pct(r.lowest_return_pct), pct(r.max_drawdown_pct),
  pct(r.hold_to_today_pct), num(r.price_days), r.price_note,
]);
const detail = addSheet(workbook, "买入明细对比", [detailHeaders, ...detailRows], { freezeRows: 1, freezeCols: 4 });
styleNumericColumns(detail, detailRows.length + 1, {
  B: "yyyy-mm-dd", F: "#,##0", G: "0.000", H: "#,##0.00", I: "#,##0.00", J: "0.000",
  K: "#,##0", L: "#,##0", N: "#,##0.00", O: "#,##0.00", P: "#,##0.00", Q: "0.00%",
  R: "yyyy-mm-dd", S: "yyyy-mm-dd", T: "0.000", U: "0.00%", V: "#,##0.00", W: "0.000",
  X: "yyyy-mm-dd", Y: "0.00%", Z: "0.000", AA: "yyyy-mm-dd", AB: "0.00%", AC: "0.00%",
  AD: "0.00%", AE: "#,##0",
});
stylePctBySign(detail, `Q2:Q${detailRows.length + 1}`);
stylePctBySign(detail, `U2:U${detailRows.length + 1}`);
stylePctBySign(detail, `Y2:AD${detailRows.length + 1}`);
setWidths(detail, { A: 10, B: 12, C: 11, D: 14, E: 14, M: 22, U: 15, V: 18, AF: 32 });

const allocHeaders = ["买入ID", "卖出ID", "证券代码", "卖出日期", "匹配数量", "卖出净价", "匹配成本", "卖出净收入", "收益"];
const allocRows = data.allocations.map((r) => [
  r.buy_trade_id, r.sell_trade_id, r.code, dt(r.sell_date), num(r.qty), num(r.sell_net_price),
  num(r.matched_cost), num(r.sell_proceeds), num(r.pnl),
]);
const alloc = addSheet(workbook, "FIFO配对", [allocHeaders, ...allocRows], { freezeRows: 1 });
styleNumericColumns(alloc, allocRows.length + 1, { D: "yyyy-mm-dd", E: "#,##0", F: "0.000", G: "#,##0.00", H: "#,##0.00", I: "#,##0.00" });
stylePctBySign(alloc, `I2:I${allocRows.length + 1}`);

const unmatchedHeaders = ["流水ID", "日期", "证券代码", "证券名称", "未匹配数量", "卖出净收入", "说明"];
const unmatchedRows = data.unmatched_sells.map((r) => [r.trade_id, dt(r.date), r.code, r.name, num(r.qty), num(r.net_proceeds), r.note]);
const unmatched = addSheet(workbook, "未匹配卖出", [unmatchedHeaders, ...unmatchedRows], { freezeRows: 1 });
styleNumericColumns(unmatched, unmatchedRows.length + 1, { B: "yyyy-mm-dd", E: "#,##0", F: "#,##0.00" });
setWidths(unmatched, { G: 62 });
unmatched.getRange(`G2:G${unmatchedRows.length + 1}`).format.wrapText = true;

const rawHeaders = ["流水ID", "日期", "币种", "股东账号", "证券代码", "证券名称", "摘要", "成交数量", "成交均价", "佣金", "印花税", "其他费", "发生金额", "资金余额"];
const rawRows = data.raw_trades.map((r) => [
  r.trade_id, dt(r["日期"]), r["币种"], String(r["股东账号"]), r["证券代码"], r["证券名称"], r["摘要"],
  num(r["成交数量"]), num(r["成交均价"]), num(r["佣金"]), num(r["印花税"]), num(r["其他费"]),
  num(r["发生金额"]), num(r["资金余额"]),
]);
const raw = addSheet(workbook, "原始流水", [rawHeaders, ...rawRows], { freezeRows: 1 });
styleNumericColumns(raw, rawRows.length + 1, { B: "yyyy-mm-dd", H: "#,##0", I: "0.000", J: "#,##0.00", K: "#,##0.00", L: "#,##0.00", M: "#,##0.00", N: "#,##0.00" });

const sheetsToRender = ["说明", "股票汇总", "买入明细对比", "FIFO配对", "未匹配卖出", "原始流水"];
for (const sheetName of sheetsToRender) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(path.join(outputDir, `${sheetName}.png`), bytes);
}

const check = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(check.ndjson);

const summaryCheck = await workbook.inspect({
  kind: "table",
  range: "股票汇总!A1:T12",
  include: "values",
  tableMaxRows: 12,
  tableMaxCols: 20,
});
console.log(summaryCheck.ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(outputDir, "股票交割单收益对比_20260630.xlsx"));
