# 实时数据来源（免费、无需 key、仅用 Python 标准库）

本文档提供「强制实时数据锚定」所需的可直接执行的 Python 片段。**全部仅用标准库 `urllib`（无需 pip install requests）**，在任何装了 Python3 的 PC 上都能直接跑。东财系接口已内置限流防封。所有采集前先确认交易日。

> 若环境已安装 `a-stock-data` 技能，其内嵌的同源脚本（用 requests）也可直接复用。

## 0. 代码归一化

```python
import re, json, time, random, urllib.parse, urllib.request

def norm_code(code):
    return re.sub(r'[^0-9]', '', code)

def prefix(code):
    if code.startswith(("6","9")): return "sh"
    if code.startswith("8"): return "bj"
    return "sz"

def market_prefix(code):          # 东财 secid 用
    return "1." + code if code.startswith("6") else "0." + code

_UA = {"User-Agent": "Mozilla/5.0"}

def _get(url, params=None, headers=None, timeout=15):
    if params: url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout).read()

def _post(url, data=None, headers=None, timeout=15):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={**_UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout).read()
```

## 1. 腾讯财经 API —— 实时价 / 市值 / PE / PB / 涨跌停（首选，不封 IP）

```python
def tencent_quote(codes):
    url = "https://qt.gtimg.cn/q=" + ",".join(f"{prefix(c)}{c}" for c in codes)
    raw = _get(url).decode("gbk")
    out = {}
    for line in raw.strip().split(";"):
        if "=" not in line or '"' not in line: continue
        v = line.split('"')[1].split("~")
        if len(v) < 53: continue
        code = line.split("=")[0].split("_")[-1][2:]
        out[code] = {"name": v[1], "price": float(v[3] or 0),
            "last_close": float(v[4] or 0), "change_pct": float(v[32] or 0),
            "high": float(v[33] or 0), "low": float(v[34] or 0),
            "pe_ttm": float(v[39] or 0), "mcap_yi": float(v[44] or 0),
            "float_mcap_yi": float(v[45] or 0), "pb": float(v[46] or 0),
            "limit_up": float(v[47] or 0), "limit_down": float(v[48] or 0)}
    return out
# 字段索引：3=现价,32=涨跌幅%,39=PE(TTM),44=总市值(亿),46=PB,47=涨停,48=跌停（43是振幅%不是PB）
```

## 2. 新浪财报三表 —— 资产负债表/利润表/现金流量表

```python
def sina_report(code, rtype="lrb", num=8):
    """rtype: fzb(资产) / lrb(利润) / llb(现金)"""
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    raw = _get(url, {"paperCode": f"{prefix(code)}{code}", "source": rtype,
                    "type": "0", "page": "1", "num": str(num)})
    rl = json.loads(raw)["result"]["data"]["report_list"]
    rows = []
    for p in sorted(rl.keys(), reverse=True)[:num]:
        rec = {"报告期": f"{p[:4]}-{p[4:6]}-{p[6:8]}"}
        for it in (rl[p].get("data") or []):
            if it.get("item_title") and it.get("item_value") is not None:
                rec[it["item_title"]] = it["item_value"]
        rows.append(rec)
    return rows
# 关键科目：营业收入/净利润/经营活动产生的现金流量净额/存货/应收账款/短期借款
```

## 3. 巨潮公告 —— 减持/增持/业绩/审计变动（必查实控人）

```python
def cninfo_ann(code, page_size=30):
    org = (f"gssh0{code}" if code.startswith("6")
           else f"gsbj0{code}" if code.startswith(("8","4"))
           else f"gssz0{code}")
    raw = _post("https://www.cninfo.com.cn/new/hisAnnouncement/query",
        {"stock": f"{code},{org}", "tabName": "fulltext", "pageSize": str(page_size),
         "pageNum": "1", "category": "", "seDate": ""},
        {"Content-Type": "application/x-www-form-urlencoded",
         "Referer": "https://www.cninfo.com.cn/new/disclosure"})
    items = json.loads(raw).get("announcements") or []
    out = []
    for a in items:
        ts = a.get("announcementTime")
        import datetime
        dt = datetime.datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d") if isinstance(ts,(int,float)) else ""
        out.append({"date": dt, "title": a.get("announcementTitle",""),
                    "type": a.get("announcementTypeName",""),
                    "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={a.get('announcementId','')}"})
    return out
# 减持过滤：[a for a in cninfo_ann('600519') if '减持' in a['title'] or '股份变动' in a['title']]
# 注意：cninfo 程序化接口偶发限流/超时，若请求失败，直接用 WebSearch「<股票> 实控人 减持」兜底，
# 结合 F10 股东研究确认实控人身份与动作（见 §6 清单）。
```

## 4. 东财 datacenter —— 大股东/实控人持股变动、龙虎榜、解禁、两融、资金流

```python
_em_last = [0.0]
def em_get(url, params=None, headers=None, timeout=15):
    """东财统一入口：串行限流(≥1s+抖动) 防封。"""
    wait = 1.0 - (time.time() - _em_last[0])
    if wait > 0: time.sleep(wait + random.uniform(0.1, 0.5))
    try: return _get(url, params, {"Referer": "https://quote.eastmoney.com/", **(headers or {})}, timeout)
    finally: _em_last[0] = time.time()

def em_dc(report, filt="", page_size=50, sort_cols="", sort_types="-1"):
    params = {"reportName": report, "columns": "ALL", "filter": filt,
              "pageNumber": "1", "pageSize": str(page_size), "sortColumns": sort_cols,
              "sortTypes": sort_types, "source": "WEB", "client": "WEB"}
    d = json.loads(em_get("https://datacenter-web.eastmoney.com/api/data/v1/get", params))
    return (d.get("result") or {}).get("data") or []

# 4.1 股东户数（实控人变动需结合 §3 公告 + F10 股东研究交叉确认）
# 4.2 限售解禁（未来90天）
#     from datetime import datetime, timedelta
#     end=(datetime.now()+timedelta(days=90)).strftime("%Y-%m-%d")
#     em_dc("RPT_LIFT_STAGE", filt=f'(SECURITY_CODE="{code}")(FREE_DATE>="{datetime.now():%Y-%m-%d}")(FREE_DATE<="{end}")')
# 4.3 龙虎榜： em_dc("RPT_DAILYBILLBOARD_DETAILSNEW", filt=f'(SECURITY_CODE="{code}")', page_size=20)
# 4.4 融资融券： em_dc("RPTA_WEB_RZRQ_GGMX", filt=f'(SCODE="{code}")', page_size=30)

# 4.5 个股资金流(日级,120日)
def fund_flow_120d(code):
    raw = em_get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        {"secid": market_prefix(code), "fields1": "f1,f2,f3,f7",
         "fields2": "f51,f52,f53,f54,f55", "lmt": "120"})
    return [l.split(",") for l in json.loads(raw).get("data",{}).get("klines",[])]
```

## 5. 东财研报（需立场校准，见 methodology.md 第四节）

```python
def em_reports(code, max_pages=5):
    out = []
    for p in range(1, max_pages+1):
        raw = em_get("https://reportapi.eastmoney.com/report/list",
            {"pageSize":"100","pageNo":str(p),"code":code,"beginTime":"2000-01-01",
             "endTime":"2030-01-01","fields":"","p":str(p),"pageNum":str(p),"pageNumber":str(p)},
            {"Referer":"https://data.eastmoney.com/"})
        rows = json.loads(raw).get("data") or []
        if not rows: break
        out.extend(rows)
    return out  # title, publishDate, orgSName, predictThisYearEps, predictNextYearEps, emRatingName
```

## 6. WebSearch 交叉验证清单（必做）

用 WebSearch 补充/验证（实测最稳的实时数据源，尤其市值/汇率/减持/海外对标）：

- `「<股票> 总市值 今日」` —— 交叉核对腾讯市值（来源：证券时报/新浪/东财行情页）。
- `「<股票> 实控人 减持 2025 OR 2026」` —— 红线确认（与 §3/§4 交叉）。
- `「<股票> 最新一季 财报 营收 净利」` —— 交叉核对新浪三表。
- `「<国内对标龙头> 总市值」` / `「<国外对标龙头> market cap」` —— 对标倍数（来源：companiesmarketcap / 行情页）。
- `「USD CNY 汇率 今日」` —— 汇率（注明日期）。
- `「<消息> 来源 官方」` —— 消息真伪验证。

> 所有 WebSearch 结果若与接口数据偏差 >5%，以实时结果为准并标注更新日期。
