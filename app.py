"""
NARA BID Dashboard v2 — FastAPI
실행: uvicorn app:app --reload --port 8502
"""
from fastapi import FastAPI, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from supabase import create_client, Client
from datetime import datetime, timedelta
from dotenv import load_dotenv
import json, os, pandas as pd

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

STATUS_LIST  = ["신규", "분배완료", "연락완료", "계약성사", "확인완료"]
STATUS_EMOJI = {"신규":"🔵","분배완료":"🟣","연락완료":"🟡","계약성사":"🟢","확인완료":"🔴"}
STATUS_COLOR = {"신규":"#3b82f6","분배완료":"#8b5cf6","연락완료":"#f59e0b","계약성사":"#22c55e","확인완료":"#ef4444"}
FAIL_OPTIONS = ["가격 경쟁력 부족","관계 부재","규격 미달","기간 내 대응 불가","내부 결정으로 제외","기타"]

app = FastAPI(title="NARA BID Dashboard")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────
def sb() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_session(request: Request) -> dict | None:
    raw = request.cookies.get("session")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def load_user(email: str, supabase: Client):
    try:
        res = supabase.table("user_info").select("*, companies(*)").eq("email", email).maybe_single().execute()
        if not res or not res.data:
            return None, {}
        user = res.data
        c_res = supabase.table("company_settings").select("*").eq("company_id", user.get("company_id")).maybe_single().execute()
        return user, (c_res.data if c_res else {}) or {}
    except Exception as e:
        print(f"load_user error: {e}")
        return None, {}

def require_login(request: Request):
    """세션 없으면 None 반환 → 호출부에서 redirect"""
    s = get_session(request)
    return s.get("email") if s else None

def extract_model(product_name) -> str:
    """product_name 콤마 구분 → 뒤에서 2번째가 모델명"""
    if not product_name or (isinstance(product_name, float)):
        return "미분류"
    parts = [p.strip() for p in str(product_name).split(',')]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "미분류")


# ── 인증 ──────────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    supabase = sb()
    try:
        auth = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if auth.user:
            resp = RedirectResponse("/", status_code=302)
            resp.set_cookie("session", json.dumps({"email": email}),
                            httponly=True, max_age=7 * 24 * 3600)
            return resp
    except Exception:
        pass
    return RedirectResponse("/login?error=이메일 또는 비밀번호가 틀렸습니다.", status_code=302)

@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp


# ── 메인 대시보드 ────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    email = require_login(request)
    if not email:
        return RedirectResponse("/login")
    supabase = sb()
    user, cs = load_user(email, supabase)
    if not user:
        return RedirectResponse("/login")

    company_id = cs.get("company_id") or user.get("company_id")
    yesterday  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        bid_cnt = supabase.table("nara_bid_data").select("bid_id", count="exact").eq("company_id", company_id).eq("bid_date", yesterday).execute().count or 0
        svc_cnt = supabase.table("nara_service_data").select("contract_id", count="exact").eq("company_id", company_id).eq("contract_date", yesterday).execute().count or 0
        mkt_cnt = supabase.table("nara_market_data").select("contract_id", count="exact").eq("company_id", company_id).eq("contract_date", yesterday).execute().count or 0
    except Exception:
        bid_cnt = svc_cnt = mkt_cnt = 0

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user, "cs": cs,
        "yesterday": yesterday, "bid_cnt": bid_cnt, "svc_cnt": svc_cnt, "mkt_cnt": mkt_cnt,
    })


# ── 용역현황 ─────────────────────────────────────────────────────────
@app.get("/service", response_class=HTMLResponse)
async def service_page(
    request: Request,
    s_date: str = Query(default=None),
    e_date: str = Query(default=None),
    search: str = Query(default=""),
    status_filter: str = Query(default=""),
    tab: str = Query(default="all"),
):
    email = require_login(request)
    if not email:
        return RedirectResponse("/login")
    supabase = sb()
    user, cs = load_user(email, supabase)
    if not user:
        return RedirectResponse("/login")

    company_id = cs.get("company_id") or user.get("company_id")
    s_date = s_date or (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    e_date = e_date or datetime.now().strftime("%Y-%m-%d")

    # 전체 DB 데이터 범위 조회
    svc_oldest = (supabase.table("nara_service_data").select("contract_date")
                  .eq("company_id", company_id).not_.is_("contract_date", "null")
                  .order("contract_date", desc=False).limit(1).execute())
    svc_newest = (supabase.table("nara_service_data").select("contract_date")
                  .eq("company_id", company_id).not_.is_("contract_date", "null")
                  .order("contract_date", desc=True).limit(1).execute())
    svc_total  = (supabase.table("nara_service_data").select("contract_date", count="exact")
                  .eq("company_id", company_id).execute())
    db_range_svc = {
        "oldest": (svc_oldest.data[0]["contract_date"] if svc_oldest.data else None),
        "newest": (svc_newest.data[0]["contract_date"] if svc_newest.data else None),
        "total":  svc_total.count or 0,
    }

    res = (supabase.table("nara_service_data").select("*")
           .eq("company_id", company_id)
           .gte("contract_date", s_date).lte("contract_date", e_date)
           .order("contract_date", desc=True).execute())
    data = res.data or []

    # 상태 기본값 채우기
    for d in data:
        if not d.get("status"):
            d["status"] = "신규"

    # 필터링
    if search:
        q = search.lower()
        data = [d for d in data if q in (d.get("contract_name","") + d.get("agency","")).lower()]
    if status_filter:
        filters = [f.strip() for f in status_filter.split(",") if f.strip()]
        data = [d for d in data if d.get("status") in filters]

    # 담당자 목록
    mgr_res = supabase.table("user_info").select("name").eq("company_id", company_id).execute()
    managers = ["미정"] + [u["name"] for u in (mgr_res.data or []) if u.get("name")]

    # 통계
    my_name = user.get("name", "")
    # 구버전 상태 마이그레이션
    for d in data:
        if d.get("status") in ("계약불발",):
            d["status"] = "확인완료"
        elif d.get("status") in ("연락중", "미팅완료"):
            d["status"] = "연락완료"

    stats = {
        "total":          len(data),
        "success":        sum(1 for d in data if d.get("status") == "계약성사"),
        "fail":           sum(1 for d in data if d.get("status") == "확인완료"),
        "active":         sum(1 for d in data if d.get("status") == "연락완료"),
        "success_amount": sum(d.get("amount",0) or 0 for d in data if d.get("status") == "계약성사"),
        "mine":           sum(1 for d in data if d.get("claimed_by") == my_name),
        "new":            sum(1 for d in data if d.get("status") == "신규"),
    }

    mine_data = [d for d in data if d.get("claimed_by") == my_name]
    kanban    = {s: [d for d in mine_data if d.get("status") == s] for s in STATUS_LIST}

    return templates.TemplateResponse(request, "service.html", {
        "user": user, "cs": cs,
        "data": data, "mine_data": mine_data, "kanban": kanban,
        "managers": managers, "stats": stats,
        "s_date": s_date, "e_date": e_date, "search": search,
        "status_filter": status_filter, "active_tab": tab,
        "db_range": db_range_svc,
        "STATUS_LIST": STATUS_LIST, "STATUS_EMOJI": STATUS_EMOJI, "STATUS_COLOR": STATUS_COLOR,
        "FAIL_OPTIONS": FAIL_OPTIONS,
        "status_bg": {
            "신규":    "bg-blue-100 text-blue-700",
            "분배완료": "bg-purple-100 text-purple-700",
            "연락완료": "bg-yellow-100 text-yellow-700",
            "계약성사":"bg-green-100 text-green-700",
            "확인완료":"bg-red-100 text-red-700",
        },
    })

@app.post("/api/service/update/{record_id}")
async def update_service(record_id: str, request: Request):
    if not require_login(request):
        raise HTTPException(401)
    body = await request.json()
    supabase = sb()
    try:
        supabase.table("nara_service_data").update({
            "status":        body.get("status"),
            "claimed_by":    body.get("claimed_by"),
            "manager":       body.get("claimed_by"),
            "fail_reason":   body.get("fail_reason",""),
            "is_external":   body.get("is_external", False),
            "external_name": body.get("external_name",""),
            "remarks":       body.get("remarks",""),
            "updated_at":    datetime.now().isoformat(),
        }).eq("id", record_id).execute()
    except Exception as e:
        print(f"update_service error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True}

@app.post("/api/service/delete/{record_id}")
async def delete_service(record_id: str, request: Request):
    if not require_login(request):
        raise HTTPException(401)
    supabase = sb()
    supabase.table("nara_service_data").delete().eq("id", record_id).execute()
    return {"ok": True}

@app.post("/api/service/claim/{record_id}")
async def claim_service(record_id: str, request: Request):
    email = require_login(request)
    if not email:
        raise HTTPException(401)
    supabase = sb()
    user, _ = load_user(email, supabase)
    if not user:
        raise HTTPException(401)
    try:
        supabase.table("nara_service_data").update({
            "claimed_by": user.get("name"),
            "manager":    user.get("name"),
            "status":     "분배완료",
            "updated_at": datetime.now().isoformat(),
        }).eq("id", record_id).execute()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "name": user.get("name")}

@app.get("/api/service/regions")
async def get_regions(request: Request):
    email = require_login(request)
    if not email:
        raise HTTPException(401)
    supabase = sb()
    res = supabase.table("user_info").select("my_regions").eq("email", email).maybe_single().execute()
    raw = (res.data or {}).get("my_regions") if res else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    return {"regions": raw or []}

@app.post("/api/service/regions")
async def save_regions(request: Request):
    email = require_login(request)
    if not email:
        raise HTTPException(401)
    body = await request.json()
    regions = body.get("regions", [])
    supabase = sb()
    supabase.table("user_info").update({"my_regions": json.dumps(regions, ensure_ascii=False)}).eq("email", email).execute()
    return {"ok": True}

@app.get("/api/service/region")
async def region_analysis(request: Request, region: str = Query(...)):
    """region: 쉼표로 구분된 복수 지역 지원 (예: 서울,경기)"""
    email = require_login(request)
    if not email:
        raise HTTPException(401)
    supabase = sb()
    user, cs = load_user(email, supabase)
    company_id = cs.get("company_id") or user.get("company_id")

    regions = [r.strip() for r in region.split(",") if r.strip()]

    all_rows: list = []
    for reg in regions:
        res = (supabase.table("nara_service_data")
               .select("id, company_name, agency, amount, claimed_by, status, contract_name, contract_date")
               .eq("company_id", company_id)
               .ilike("agency", f"{reg}%")
               .execute())
        all_rows.extend(res.data or [])

    # 중복 제거 (id 기준)
    seen: set = set()
    rows: list = []
    for r in all_rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            rows.append(r)

    stats: dict = {}
    for r in rows:
        name = r.get("company_name") or "미분류"
        stats.setdefault(name, {"amount": 0, "count": 0})
        stats[name]["amount"] += r.get("amount") or 0
        stats[name]["count"]  += 1

    sorted_stats = sorted(stats.items(), key=lambda x: x[1]["amount"], reverse=True)[:15]

    # 미배정 건 목록
    unassigned = [
        {"id": r["id"], "contract_name": r.get("contract_name",""), "agency": r.get("agency","")}
        for r in rows
        if not r.get("claimed_by") or r.get("claimed_by") in ("", "미정")
    ]

    return {
        "region":     region,
        "total":      len(rows),
        "unassigned_count": len(unassigned),
        "unassigned_ids": [u["id"] for u in unassigned],
        "labels":     [s[0] for s in sorted_stats],
        "amounts":    [round(s[1]["amount"] / 10000) for s in sorted_stats],
        "counts":     [s[1]["count"] for s in sorted_stats],
    }

@app.post("/api/service/region/assign")
async def region_assign(request: Request):
    """선택 지역 미배정 건 일괄 자동 분배"""
    email = require_login(request)
    if not email:
        raise HTTPException(401)
    supabase = sb()
    user, _ = load_user(email, supabase)
    body = await request.json()
    ids: list = body.get("ids", [])
    my_name = user.get("name", "")
    try:
        for rid in ids:
            supabase.table("nara_service_data").update({
                "claimed_by": my_name,
                "manager":    my_name,
                "status":     "분배완료",
                "updated_at": datetime.now().isoformat(),
            }).eq("id", rid).execute()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "count": len(ids), "name": my_name}

@app.get("/api/service/company_analysis")
async def service_company_analysis(request: Request, company: str = Query(...)):
    """용역 파이프라인 업체별 분석"""
    email = require_login(request)
    if not email:
        raise HTTPException(401)
    supabase = sb()
    user, cs = load_user(email, supabase)
    company_id = cs.get("company_id") or user.get("company_id")

    res = (supabase.table("nara_service_data")
           .select("amount, status, agency, contract_date, category")
           .eq("company_id", company_id)
           .eq("company_name", company)
           .execute())
    rows = res.data or []
    if not rows:
        return {"company": company, "total": 0}

    df = pd.DataFrame(rows)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    df["contract_date"] = pd.to_datetime(df["contract_date"], errors="coerce")
    df["month"] = df["contract_date"].dt.to_period("M").astype(str)

    monthly = df.groupby("month")["amount"].sum().reset_index().sort_values("month")

    # 카테고리별 집계
    if "category" in df.columns and df["category"].notna().any():
        cat_agg = (df.groupby("category")["amount"]
                   .agg(["sum","count"]).reset_index()
                   .sort_values("sum", ascending=False))
        cat_labels  = cat_agg["category"].fillna("미분류").tolist()
        cat_amounts = (cat_agg["sum"] / 1e8).round(2).tolist()
        cat_counts  = cat_agg["count"].astype(int).tolist()
    else:
        cat_labels = cat_amounts = cat_counts = []

    # 발주처 TOP10
    agency_top = df["agency"].value_counts().head(10)
    agency_top10_labels = [a[:16] for a in agency_top.index.tolist()]
    agency_top10_counts = agency_top.values.tolist()

    return {
        "company":        company,
        "total":          len(rows),
        "total_amount":   int(df["amount"].sum()),
        "success_cnt":    int((df["status"] == "계약성사").sum()),
        "monthly_labels": monthly["month"].tolist(),
        "monthly_amounts":(monthly["amount"] / 1e8).round(2).tolist(),
        "cat_labels":     cat_labels,
        "cat_amounts":    cat_amounts,
        "cat_counts":     cat_counts,
        "agency_top10_labels": agency_top10_labels,
        "agency_top10_counts": agency_top10_counts,
    }


# ── 계약현황 ─────────────────────────────────────────────────────────
@app.get("/market", response_class=HTMLResponse)
async def market_page(
    request: Request,
    s_date: str  = Query(default=None),
    e_date: str  = Query(default=None),
    search: str  = Query(default=""),
    company_sel: str = Query(default=""),
    view_type: str   = Query(default="월별"),
):
    email = require_login(request)
    if not email:
        return RedirectResponse("/login")
    supabase = sb()
    user, cs = load_user(email, supabase)
    if not user:
        return RedirectResponse("/login")

    company_id = cs.get("company_id") or user.get("company_id")
    s_date = s_date or (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    e_date = e_date or datetime.now().strftime("%Y-%m-%d")

    # 전체 DB 데이터 범위 조회
    mkt_oldest = (supabase.table("nara_market_data").select("contract_date")
                  .eq("company_id", company_id).not_.is_("contract_date", "null")
                  .order("contract_date", desc=False).limit(1).execute())
    mkt_newest = (supabase.table("nara_market_data").select("contract_date")
                  .eq("company_id", company_id).not_.is_("contract_date", "null")
                  .order("contract_date", desc=True).limit(1).execute())
    mkt_total  = (supabase.table("nara_market_data").select("contract_date", count="exact")
                  .eq("company_id", company_id).execute())
    db_range_mkt = {
        "oldest": (mkt_oldest.data[0]["contract_date"] if mkt_oldest.data else None),
        "newest": (mkt_newest.data[0]["contract_date"] if mkt_newest.data else None),
        "total":  mkt_total.count or 0,
    }

    # 전체 데이터 로드 (페이지네이션)
    all_data, start, PAGE = [], 0, 1000
    while True:
        res = (supabase.table("nara_market_data").select("*")
               .eq("company_id", company_id)
               .order("contract_date", desc=True)
               .range(start, start + PAGE - 1).execute())
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < PAGE:
            break
        start += PAGE

    df = pd.DataFrame(all_data) if all_data else pd.DataFrame()
    report = {}
    companies = []

    if not df.empty:
        df['contract_date'] = pd.to_datetime(df['contract_date'], errors='coerce')
        df = df[(df['contract_date'] >= pd.to_datetime(s_date)) &
                (df['contract_date'] <= pd.to_datetime(e_date))]
        df['amount']     = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
        df['model_name'] = df['product_name'].apply(extract_model) if 'product_name' in df.columns else "미분류"
        df['base_id']    = df['contract_id'].apply(lambda x: str(x).split('-')[0])

        if search:
            df = df[df.apply(lambda r: search.lower() in
                             (str(r.get('contract_name','')) + str(r.get('company_name','')) + str(r.get('agency',''))).lower(), axis=1)]

        companies = sorted(df['company_name'].dropna().unique().tolist())
        target = company_sel or (companies[0] if companies else None)

        if target:
            rdf = df[df['company_name'] == target].copy()
            total_amt = int(rdf['amount'].sum())
            total_cnt = len(rdf)

            # 모델 구성 (금액 기준 억원, 건수는 정수)
            model_agg = (rdf.groupby('model_name')['amount'].agg(['sum','count'])
                         .reset_index().rename(columns={'sum':'amount','count':'cnt'})
                         .sort_values('amount', ascending=False))
            model_labels  = model_agg['model_name'].tolist()
            model_amounts = (model_agg['amount'] / 1e8).round(2).tolist()

            # 모델명별 납품 구성
            cat_agg = (rdf.groupby('model_name')['amount'].agg(['sum','count'])
                       .reset_index().rename(columns={'sum':'amount','count':'cnt'})
                       .sort_values('amount', ascending=False).head(15))
            cat_labels  = cat_agg['model_name'].fillna('미분류').tolist()
            cat_amounts = (cat_agg['amount'] / 1e8).round(2).tolist()
            cat_counts  = cat_agg['cnt'].astype(int).tolist()

            # 발주처 × 모델 (건수는 무조건 정수)
            am_df = (rdf.groupby(['agency','model_name']).size()
                     .reset_index(name='cnt'))
            am_df['cnt'] = am_df['cnt'].astype(int)
            top_agencies = (am_df.groupby('agency')['cnt'].sum()
                            .sort_values(ascending=False).head(10).index.tolist())
            am_df  = am_df[am_df['agency'].isin(top_agencies)]
            pivot  = am_df.pivot_table(index='agency', columns='model_name',
                                       values='cnt', aggfunc='sum', fill_value=0)
            pivot  = pivot.astype(int)   # 모든 값 정수 보장
            pivot  = pivot.loc[pivot.sum(axis=1).sort_values(ascending=True).index]
            models = pivot.columns.tolist()
            agency_labels   = [a[:14] for a in pivot.index.tolist()]
            agency_datasets = [
                {"label": m, "data": pivot[m].astype(int).tolist()}
                for m in models
            ]

            # 시계열
            if view_type == "월별":
                rdf['period'] = rdf['contract_date'].dt.to_period('M').astype(str)
            else:
                rdf['period'] = rdf['contract_date'].dt.year.astype(str) + "년"
            trend = (rdf.groupby('period')['amount'].sum().reset_index()
                     .sort_values('period'))
            trend_labels  = trend['period'].tolist()
            trend_amounts = (trend['amount'] / 1e8).round(2).tolist()

            top_agency = rdf['agency'].value_counts().index[0] if total_cnt > 0 else "-"
            first_date = rdf['contract_date'].min()
            last_date  = rdf['contract_date'].max()

            # 발주처 TOP10 (건수 기준 단순 bar)
            agency_cnt = rdf['agency'].value_counts().head(10)
            agency_top10_labels = [a[:16] for a in agency_cnt.index.tolist()]
            agency_top10_counts = agency_cnt.values.tolist()

            report = {
                "company":       target,
                "total_amount":  total_amt,
                "total_count":   total_cnt,
                "avg_amount":    int(rdf['amount'].mean()) if total_cnt > 0 else 0,
                "top_agency":    top_agency,
                "period":        f"{first_date.strftime('%y.%m') if pd.notna(first_date) else '?'}~{last_date.strftime('%y.%m') if pd.notna(last_date) else '?'}",
                "cat_labels":    json.dumps(cat_labels, ensure_ascii=False),
                "cat_amounts":   json.dumps(cat_amounts),
                "cat_counts":    json.dumps(cat_counts),
                "trend_labels":  json.dumps(trend_labels, ensure_ascii=False),
                "trend_amounts": json.dumps(trend_amounts),
                "agency_top10_labels": json.dumps(agency_top10_labels, ensure_ascii=False),
                "agency_top10_counts": json.dumps(agency_top10_counts),
            }

    table_data = df.head(300).to_dict('records') if not df.empty else []
    # 날짜를 문자열로 변환
    for row in table_data:
        if pd.notna(row.get('contract_date')):
            row['contract_date'] = str(row['contract_date'])[:10]

    return templates.TemplateResponse(request, "market.html", {
        "user": user, "cs": cs,
        "data": table_data, "companies": companies,
        "company_sel": company_sel or (companies[0] if companies else ""),
        "report": report,
        "s_date": s_date, "e_date": e_date, "search": search,
        "view_type": view_type, "total_rows": len(df) if not df.empty else 0,
        "db_range": db_range_mkt,
    })


# ── 계약현황 지역분석 API ─────────────────────────────────────────────
@app.get("/api/market/region")
async def market_region_analysis(request: Request, region: str = Query(...)):
    """계약현황 지역분석: region 쉼표 구분 복수 지원"""
    email = require_login(request)
    if not email:
        raise HTTPException(401)
    supabase = sb()
    user, cs = load_user(email, supabase)
    company_id = cs.get("company_id") or user.get("company_id")

    regions = [r.strip() for r in region.split(",") if r.strip()]

    all_rows: list = []
    for reg in regions:
        res = (supabase.table("nara_market_data")
               .select("id, company_name, agency, amount, contract_date")
               .eq("company_id", company_id)
               .ilike("agency", f"{reg}%")
               .execute())
        all_rows.extend(res.data or [])

    seen: set = set()
    rows: list = []
    for r in all_rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            rows.append(r)

    stats: dict = {}
    for r in rows:
        name = r.get("company_name") or "미분류"
        stats.setdefault(name, {"amount": 0, "count": 0})
        stats[name]["amount"] += r.get("amount") or 0
        stats[name]["count"]  += 1

    sorted_stats = sorted(stats.items(), key=lambda x: x[1]["amount"], reverse=True)[:15]

    return {
        "region": region,
        "total":  len(rows),
        "labels":  [s[0] for s in sorted_stats],
        "amounts": [round(s[1]["amount"] / 10000) for s in sorted_stats],
        "counts":  [s[1]["count"] for s in sorted_stats],
    }


# ── 입찰공고 ─────────────────────────────────────────────────────────
@app.get("/bid", response_class=HTMLResponse)
async def bid_page(
    request: Request,
    s_date: str = Query(default=None),
    e_date: str = Query(default=None),
    search: str = Query(default=""),
    sort:   str = Query(default="최신순"),
):
    email = require_login(request)
    if not email:
        return RedirectResponse("/login")
    supabase = sb()
    user, cs = load_user(email, supabase)
    if not user:
        return RedirectResponse("/login")

    company_id = cs.get("company_id") or user.get("company_id")
    s_date = s_date or (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    e_date = e_date or datetime.now().strftime("%Y-%m-%d")

    # 전체 DB 데이터 범위 조회
    bid_oldest = (supabase.table("nara_bid_data").select("bid_date")
                  .eq("company_id", company_id).not_.is_("bid_date", "null")
                  .order("bid_date", desc=False).limit(1).execute())
    bid_newest = (supabase.table("nara_bid_data").select("bid_date")
                  .eq("company_id", company_id).not_.is_("bid_date", "null")
                  .order("bid_date", desc=True).limit(1).execute())
    bid_total  = (supabase.table("nara_bid_data").select("bid_date", count="exact")
                  .eq("company_id", company_id).execute())
    db_range_bid = {
        "oldest": (bid_oldest.data[0]["bid_date"] if bid_oldest.data else None),
        "newest": (bid_newest.data[0]["bid_date"] if bid_newest.data else None),
        "total":  bid_total.count or 0,
    }

    res = (supabase.table("nara_bid_data").select("*")
           .eq("company_id", company_id)
           .gte("bid_date", s_date).lte("bid_date", e_date)
           .order("bid_date", desc=True).execute())
    data = res.data or []

    if search:
        q = search.lower()
        data = [d for d in data if q in (d.get("title","") + d.get("agency","")).lower()]

    sort_key = {"최신순": lambda x: x.get("bid_date",""), "높은금액순": lambda x: -(x.get("budget") or 0), "기관명순": lambda x: x.get("agency","")}
    if sort in sort_key:
        data.sort(key=sort_key[sort], reverse=(sort == "최신순"))

    total_budget = sum(d.get("budget") or 0 for d in data)

    return templates.TemplateResponse(request, "bid.html", {
        "user": user, "cs": cs,
        "data": data, "s_date": s_date, "e_date": e_date,
        "search": search, "sort": sort,
        "total_count": len(data),
        "total_budget": total_budget,
        "db_range": db_range_bid,
    })


# ── 마이페이지 ────────────────────────────────────────────────────────
@app.get("/mypage", response_class=HTMLResponse)
async def mypage(request: Request):
    email = require_login(request)
    if not email:
        return RedirectResponse("/login")
    supabase = sb()
    user, cs = load_user(email, supabase)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "mypage.html", {
        "user": user, "cs": cs,
    })

@app.post("/api/mypage/keywords")
async def save_keywords(request: Request):
    email = require_login(request)
    if not email:
        raise HTTPException(401)
    supabase = sb()
    user, cs = load_user(email, supabase)
    body = await request.json()
    company_id = cs.get("company_id") or user.get("company_id")
    supabase.table("company_settings").upsert({
        "company_id": company_id,
        "bid_kw": body.get("bid_kw",""), "bid_ex": body.get("bid_ex",""),
        "svc_kw": body.get("svc_kw",""), "svc_ex": body.get("svc_ex",""),
        "mkt_kw": body.get("mkt_kw",""), "mkt_ex": body.get("mkt_ex",""),
    }, on_conflict="company_id").execute()
    return {"ok": True}


# ── 수동 데이터 수집 ──────────────────────────────────────────────────
@app.post("/api/mypage/collect")
async def manual_collect(request: Request):
    """수동 데이터 수집 (용역/계약/입찰)"""
    import requests as req_lib
    import time as t_lib
    from urllib.parse import quote, unquote

    email = require_login(request)
    if not email:
        raise HTTPException(401)
    supabase = sb()
    user, cs = load_user(email, supabase)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    collect_type = body.get("type", "service")   # service | market | bid
    s_date = body.get("s_date", (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
    e_date = body.get("e_date", datetime.now().strftime("%Y-%m-%d"))

    nara_key_raw = os.getenv("NARA_KEY", "")
    nara_key = unquote(nara_key_raw) if nara_key_raw else ""
    if not nara_key:
        return JSONResponse({"ok": False, "error": "NARA_KEY 환경변수가 설정되지 않았습니다.", "count": 0})

    company_id = cs.get("company_id") or user.get("company_id")
    logs = []

    def _extract_items(body_data):
        raw = body_data.get("items", [])
        if not raw:
            return []
        if isinstance(raw, dict):
            inner = raw.get("item", [])
            if not inner:
                return []
            return [inner] if isinstance(inner, dict) else list(inner)
        return raw if isinstance(raw, list) else []

    def _protected_key():
        return quote(unquote(str(nara_key)))

    session = req_lib.Session()

    try:
        # ── 용역 수집 ──────────────────────────────────────────────
        if collect_type == "service":
            kw_raw = (cs.get("svc_kw") or "").strip()
            ex_raw = (cs.get("svc_ex") or "").strip()
            if not kw_raw:
                return JSONResponse({"ok": False, "error": "용역 키워드가 설정되지 않았습니다.", "count": 0})

            keywords = []
            for line in kw_raw.splitlines():
                line = line.strip()
                if not line: continue
                kw_part = line.split(":", 1)[1] if ":" in line else line
                keywords.extend(k.strip() for k in kw_part.split(",") if k.strip())
            keywords = list(set(keywords))
            exceptions = [e.strip() for e in ex_raw.split(",") if e.strip()]

            s_dt = s_date.replace("-", "")
            e_dt = e_date.replace("-", "")
            URL = "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListServcPPSSrch"
            URL_BIZ = "https://apis.data.go.kr/1230000/ao/UsrInfoService02/getPrcrmntCorpBasicInfo02"
            biz_cache: dict = {}
            batch: dict = {}

            for kw in keywords:
                page = 1
                while True:
                    try:
                        url = (f"{URL}?serviceKey={_protected_key()}"
                               f"&pageNo={page}&numOfRows=100&inqryDiv=1&type=json"
                               f"&inqryBgnDate={s_dt}&inqryEndDate={e_dt}"
                               f"&cntrctNm={quote(kw)}")
                        res = session.get(url, timeout=60)
                        if res.status_code == 502:
                            t_lib.sleep(5); continue
                        res.raise_for_status()
                        items = _extract_items(res.json().get("response", {}).get("body", {}))
                        if not items: break
                        for item in items:
                            c_id = str(item.get("untyCntrctNo") or item.get("cntrctNo") or "").strip()
                            c_name = item.get("cntrctNm", "") or ""
                            if not c_id or any(ex in c_name for ex in exceptions): continue
                            if c_id in batch: continue
                            raw_corp = item.get("corpList", "") or ""
                            parts = raw_corp.split("^") if "^" in raw_corp else []
                            corp_name = parts[3].strip() if len(parts) > 3 else raw_corp.strip()
                            biz_no = "".join(filter(str.isdigit, parts[9])) if len(parts) > 9 else ""
                            if biz_no and biz_no not in biz_cache:
                                try:
                                    br = session.get(
                                        f"{URL_BIZ}?serviceKey={_protected_key()}&pageNo=1&numOfRows=1&inqryDiv=3&bizno={biz_no}&type=json",
                                        timeout=10)
                                    bi = _extract_items(br.json().get("response", {}).get("body", {}))
                                    if bi:
                                        biz_cache[biz_no] = {
                                            "phone": str(bi[0].get("telNo", "") or "").strip(),
                                            "address": (str(bi[0].get("adrs", "") or "") + " " + str(bi[0].get("dtlAdrs", "") or "")).strip()
                                        }
                                    else:
                                        biz_cache[biz_no] = {"phone": "", "address": ""}
                                except Exception:
                                    biz_cache[biz_no] = {"phone": "", "address": ""}
                            biz_info = biz_cache.get(biz_no, {"phone": "", "address": ""})
                            agency = item.get("cntrctInsttNm") or ""
                            if not agency:
                                dm = item.get("dminsttList", "") or ""
                                dmp = dm.split("^")
                                agency = dmp[2].strip() if len(dmp) > 2 else ""
                            try: amount = int(item.get("thtmCntrctAmt") or 0)
                            except: amount = 0
                            batch[c_id] = {
                                "company_id": company_id, "contract_id": c_id,
                                "contract_date": item.get("cntrctCnclsDate"),
                                "contract_name": c_name, "agency": agency, "amount": amount,
                                "company_name": corp_name,
                                "company_phone": biz_info["phone"], "company_address": biz_info["address"],
                                "manager": "미정", "is_confirmed": False,
                                "remarks": "", "updated_at": datetime.now().isoformat(),
                            }
                        if len(items) < 100: break
                        page += 1; t_lib.sleep(0.3)
                    except Exception as e:
                        logs.append(f"오류: {kw} {page}p - {e}"); break

            if batch:
                existing = supabase.table("nara_service_data").select("id,contract_id,remarks,manager,claimed_by,status").eq("company_id", company_id).execute()
                lk = {r["contract_id"]: r for r in (existing.data or []) if r.get("contract_id")}
                for item in batch.values():
                    ex = lk.get(item["contract_id"])
                    if ex:
                        for f in ("remarks", "manager", "claimed_by", "status"):
                            if ex.get(f) not in (None, "", False): item[f] = ex[f]
                        if ex.get("id"): item["id"] = ex["id"]
                try:
                    supabase.table("nara_service_data").upsert(list(batch.values()), on_conflict="contract_id").execute()
                except Exception:
                    supabase.table("nara_service_data").upsert(list(batch.values()), on_conflict="company_id,contract_name,contract_date,amount").execute()
            logs.append(f"용역 수집 완료: {len(batch)}건")
            return JSONResponse({"ok": True, "count": len(batch), "logs": logs})

        # ── 계약 수집 ──────────────────────────────────────────────
        elif collect_type == "market":
            kw_raw = (cs.get("mkt_kw") or "").strip()
            if not kw_raw:
                return JSONResponse({"ok": False, "error": "계약 키워드가 설정되지 않았습니다.", "count": 0})
            keywords = []
            for line in kw_raw.splitlines():
                line = line.strip()
                if not line: continue
                kws = line.split(":")[1] if ":" in line else line
                keywords.extend(k.strip() for k in kws.split(",") if k.strip())
            keywords = list(set(keywords))

            URL = "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/getSpcifyPrdlstPrcureInfoList"
            batch: dict = {}

            # 날짜 범위를 30일 단위로 분할
            from datetime import date as _date
            _s = _date.fromisoformat(s_date)
            _e = _date.fromisoformat(e_date)
            date_ranges = []
            cur = _s
            while cur <= _e:
                chunk_end = min(cur + timedelta(days=29), _e)
                date_ranges.append((cur.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
                cur = chunk_end + timedelta(days=1)

            logs.append(f"키워드 {len(keywords)}개: {', '.join(keywords[:5])}")
            logs.append(f"조회 기간: {s_date} ~ {e_date} ({len(date_ranges)}개 구간)")
            for s_dt, e_dt in date_ranges:
                for kw in keywords:
                    page = 1
                    while True:
                        try:
                            params = {"serviceKey": nara_key, "type": "json", "numOfRows": "100",
                                      "pageNo": str(page), "inqryDiv": "1", "inqryPrdctDiv": "1",
                                      "inqryBgnDate": s_dt, "inqryEndDate": e_dt, "prdctClsfcNoNm": kw}
                            res = session.get(URL, params=params, timeout=120)
                            if res.status_code == 502:
                                t_lib.sleep(10); continue
                            res.raise_for_status()
                            rjson = res.json()
                            bdy = rjson.get("response", {}).get("body", {})
                            total_count = bdy.get("totalCount", 0)
                            raw_items = bdy.get("items", {})
                            if page == 1:
                                logs.append(f"[{kw}/{s_dt[:6]}] totalCount={total_count}")
                            items = (raw_items.get("item", []) if isinstance(raw_items, dict) else raw_items) or []
                            if isinstance(items, dict): items = [items]
                            if not items: break
                            for item in items:
                                req_no = item.get("cntrctDlvrReqNo")
                                if not req_no: continue
                                cid = f"{req_no}-{item.get('cntrctDlvrReqChgOrd')}-{item.get('prdctIdntNo')}"
                                raw_date = item.get("cntrctDlvrReqDate") or ""
                                clean_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) >= 8 else None
                                try: amount = int(round(float(str(item.get("incdecAmt") or 0).replace(",", ""))))
                                except: amount = 0
                                try: unit_price = int(round(float(str(item.get("prdctUprc") or 0).replace(",", ""))))
                                except: unit_price = 0
                                try: quantity = float(item.get("incdecQty") or 0)
                                except: quantity = 0.0
                                batch[cid] = {
                                    "company_id": company_id, "contract_id": cid,
                                    "contract_date": clean_date,
                                    "category": kw,
                                    "contract_name": item.get("cntrctDlvrReqNm", ""),
                                    "agency": item.get("dminsttNm", ""),
                                    "amount": amount,
                                    "company_name": item.get("corpNm", ""),
                                    "product_name": item.get("prdctIdntNoNm", ""),
                                    "item_name": item.get("dtilPrdctClsfcNoNm", ""),
                                    "unit_price": unit_price,
                                    "quantity": quantity,
                                    "unit": item.get("prdctUnit", ""),
                                    "is_excellent": item.get("exclcProdctYn", ""),
                                    "updated_at": datetime.now().isoformat(),
                                }
                            if len(items) < 100: break
                            page += 1; t_lib.sleep(1.0)
                        except Exception as e:
                            logs.append(f"오류: {kw} {page}p - {e}"); break

            saved = 0
            if batch:
                try:
                    supabase.table("nara_market_data").upsert(
                        list(batch.values()), on_conflict="company_id,contract_id"
                    ).execute()
                    saved = len(batch)
                except Exception:
                    try:
                        supabase.table("nara_market_data").upsert(
                            list(batch.values()), on_conflict="contract_id"
                        ).execute()
                        saved = len(batch)
                    except Exception as ex2:
                        logs.append(f"저장 오류: {ex2}")
                        return JSONResponse({"ok": False, "error": str(ex2), "count": 0, "logs": logs})
            logs.append(f"계약 수집 완료: {saved}건")
            return JSONResponse({"ok": True, "count": saved, "logs": logs})

        # ── 입찰 수집 ──────────────────────────────────────────────
        elif collect_type == "bid":
            kw_raw = (cs.get("bid_kw") or "").strip()
            ex_raw = (cs.get("bid_ex") or "").strip()
            if not kw_raw:
                return JSONResponse({"ok": False, "error": "입찰 키워드가 설정되지 않았습니다.", "count": 0})

            category_map: dict = {}
            for line in kw_raw.splitlines():
                line = line.strip()
                if not line: continue
                if ":" in line:
                    cat, kws = line.split(":", 1)
                    category_map[cat.strip()] = [k.strip() for k in kws.split(",") if k.strip()]
                else:
                    category_map["기타"] = [k.strip() for k in line.split(",") if k.strip()]
            exclude_list = [e.strip() for e in ex_raw.split(",") if e.strip()]

            # 날짜 범위를 30일 단위로 분할
            from datetime import date as _date
            _s = _date.fromisoformat(s_date)
            _e = _date.fromisoformat(e_date)
            date_ranges = []
            cur = _s
            while cur <= _e:
                chunk_end = min(cur + timedelta(days=29), _e)
                date_ranges.append((cur.strftime("%Y%m%d") + "0000", chunk_end.strftime("%Y%m%d") + "2359"))
                cur = chunk_end + timedelta(days=1)
            logs.append(f"날짜 {len(date_ranges)}개 구간으로 분할 수집")

            BID_URLS = {
                "용역": os.getenv("NARA_BID_URL_SERVICE",
                    "http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServcPPSSrch"),
                "물품": os.getenv("NARA_BID_URL_GOODS",
                    "http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoThngPPSSrch"),
                "공사": os.getenv("NARA_BID_URL_WORK",
                    "http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwkPPSSrch"),
                "기타": os.getenv("NARA_BID_URL_ETC",
                    "http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoEtcPPSSrch"),
            }
            batch: dict = {}
            for bid_type, base_url in BID_URLS.items():
                for s_dt, e_dt in date_ranges:
                    for cat_name, kw_list in category_map.items():
                        for kw in kw_list:
                            page = 1
                            while True:
                                try:
                                    params = {
                                        "serviceKey": nara_key, "type": "json",
                                        "numOfRows": "300", "pageNo": str(page),
                                        "inqryBgnDt": s_dt, "inqryEndDt": e_dt,
                                        "inqryDiv": "1", "bidNtceNm": kw
                                    }
                                    res = session.get(base_url, params=params, timeout=60)
                                    if res.status_code == 502:
                                        t_lib.sleep(5); continue
                                    res.raise_for_status()
                                    rjson = res.json()
                                    items_raw = rjson.get("response", {}).get("body", {}).get("items", [])
                                    items = (items_raw.get("item", []) if isinstance(items_raw, dict) else items_raw) or []
                                    if isinstance(items, dict): items = [items]
                                    if not items: break
                                    for item in items:
                                        bid_id = item.get("bidNtceNo")
                                        title  = item.get("bidNtceNm", "") or ""
                                        if not bid_id or not title: continue
                                        if any(ex.lower() in title.lower() for ex in exclude_list): continue
                                        agency = item.get("dminsttNm") or item.get("demandOrgNm") or "기관명미상"
                                        try: budget = int(float(item.get("bdgtAmt") or item.get("assignAmt") or 0))
                                        except: budget = 0
                                        bid_date_raw = item.get("bidNtceDt") or item.get("bidNtceBgn") or ""
                                        bid_date = bid_date_raw[:10] if bid_date_raw else None
                                        batch[bid_id] = {
                                            "company_id": company_id, "bid_id": bid_id,
                                            "type": bid_type, "category": cat_name,
                                            "title": title, "agency": agency,
                                            "budget": budget, "bid_date": bid_date,
                                            "url": item.get("bidNtceDtlUrl", ""),
                                            "updated_at": datetime.now().isoformat(),
                                        }
                                    if len(items) < 300: break
                                    page += 1; t_lib.sleep(0.3)
                                except Exception as e:
                                    logs.append(f"오류: {kw}({bid_type}) {page}p - {e}"); break

            saved_bid = 0
            if batch:
                try:
                    supabase.table("nara_bid_data").upsert(list(batch.values()), on_conflict="bid_id").execute()
                    saved_bid = len(batch)
                except Exception as ex:
                    logs.append(f"저장 오류: {ex}")
                    return JSONResponse({"ok": False, "error": str(ex), "count": 0, "logs": logs})
            logs.append(f"입찰 수집 완료: {saved_bid}건")
            return JSONResponse({"ok": True, "count": saved_bid, "logs": logs})

        else:
            return JSONResponse({"ok": False, "error": f"지원하지 않는 수집 유형: {collect_type}", "count": 0})

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "count": 0})
