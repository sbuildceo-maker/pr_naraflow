-- ============================================================
-- NARA BID Dashboard v2 — 초기 스키마 생성
-- Supabase SQL Editor에서 실행하세요
-- ============================================================

-- [1] companies 테이블
CREATE TABLE IF NOT EXISTS companies (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- [2] user_info 테이블 (Supabase Auth와 연동)
CREATE TABLE IF NOT EXISTS user_info (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email        TEXT UNIQUE NOT NULL,
  name         TEXT DEFAULT '',
  role         TEXT DEFAULT 'user',
  company_id   UUID REFERENCES companies(id),
  phone_number TEXT DEFAULT '',
  created_at   TIMESTAMPTZ DEFAULT now()
);

-- [3] company_settings 테이블
CREATE TABLE IF NOT EXISTS company_settings (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID UNIQUE REFERENCES companies(id),
  bid_kw     TEXT DEFAULT '',
  bid_ex     TEXT DEFAULT '',
  svc_kw     TEXT DEFAULT '',
  svc_ex     TEXT DEFAULT '',
  mkt_kw     TEXT DEFAULT '',
  mkt_ex     TEXT DEFAULT '',
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- [4] nara_service_data 테이블 (용역 계약)
CREATE TABLE IF NOT EXISTS nara_service_data (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id       UUID REFERENCES companies(id),
  contract_id      TEXT UNIQUE NOT NULL,
  contract_date    DATE,
  contract_name    TEXT DEFAULT '',
  agency           TEXT DEFAULT '',
  amount           BIGINT DEFAULT 0,
  company_name     TEXT DEFAULT '',
  company_phone    TEXT DEFAULT '',
  company_address  TEXT DEFAULT '',
  manager          TEXT DEFAULT '미정',
  claimed_by       TEXT DEFAULT '',
  status           TEXT DEFAULT '신규'
                   CHECK (status IN ('신규','연락완료','계약성사','확인완료')),
  remarks          TEXT DEFAULT '',
  fail_reason      TEXT DEFAULT '',
  is_external      BOOLEAN DEFAULT FALSE,
  external_name    TEXT DEFAULT '',
  is_confirmed     BOOLEAN DEFAULT FALSE,
  updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_svc_company_id   ON nara_service_data(company_id);
CREATE INDEX IF NOT EXISTS idx_svc_contract_date ON nara_service_data(contract_date);
CREATE INDEX IF NOT EXISTS idx_svc_status        ON nara_service_data(status);
CREATE INDEX IF NOT EXISTS idx_svc_claimed_by    ON nara_service_data(claimed_by);

-- [5] nara_market_data 테이블 (쇼핑몰 계약)
CREATE TABLE IF NOT EXISTS nara_market_data (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id       UUID REFERENCES companies(id),
  contract_id      TEXT NOT NULL,
  contract_date    DATE,
  category         TEXT DEFAULT '',
  contract_name    TEXT DEFAULT '',
  agency           TEXT DEFAULT '',
  amount           BIGINT DEFAULT 0,
  company_name     TEXT DEFAULT '',
  product_name     TEXT DEFAULT '',
  item_name        TEXT DEFAULT '',
  unit_price       BIGINT DEFAULT 0,
  quantity         FLOAT DEFAULT 0,
  unit             TEXT DEFAULT '',
  is_excellent     TEXT DEFAULT '',
  updated_at       TIMESTAMPTZ DEFAULT now(),
  UNIQUE (company_id, contract_id)
);

CREATE INDEX IF NOT EXISTS idx_mkt_company_id    ON nara_market_data(company_id);
CREATE INDEX IF NOT EXISTS idx_mkt_contract_date ON nara_market_data(contract_date);

-- [6] nara_bid_data 테이블 (입찰 공고)
CREATE TABLE IF NOT EXISTS nara_bid_data (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  UUID REFERENCES companies(id),
  bid_id      TEXT UNIQUE NOT NULL,
  bid_date    DATE,
  title       TEXT DEFAULT '',
  agency      TEXT DEFAULT '',
  budget      BIGINT DEFAULT 0,
  type        TEXT DEFAULT '',
  category    TEXT DEFAULT '',
  url         TEXT DEFAULT '',
  updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bid_company_id ON nara_bid_data(company_id);
CREATE INDEX IF NOT EXISTS idx_bid_date       ON nara_bid_data(bid_date);

-- ============================================================
-- [7] 초기 회사 및 사용자 등록 (로그인 후 수동으로 insert)
-- ※ Supabase Auth에서 먼저 회원가입 후 아래 실행
-- ============================================================
-- INSERT INTO companies (name) VALUES ('회사명') RETURNING id;
-- INSERT INTO user_info (email, name, role, company_id)
--   VALUES ('이메일', '이름', 'admin', '위에서 반환된 UUID');
-- INSERT INTO company_settings (company_id)
--   VALUES ('위에서 반환된 UUID');
