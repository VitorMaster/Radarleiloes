-- Radar de Leilões — esquema de dados
-- Um lote = um bem em leilão numa fonte. Campos comuns ficam em `lots`;
-- blocos específicos por categoria ficam em `lot_imovel` / `lot_veiculo`.
-- `lot_observations` é append-only: guarda cada mudança de preço/status
-- desde o primeiro dia, para montar o histórico de "preço realizado".

CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,          -- ex.: 'caixa'
    name            TEXT NOT NULL,
    layer           INTEGER NOT NULL,          -- camada do mapa de fontes (1 a 4)
    leiloeiro       TEXT,                      -- leiloeiro/órgão responsável
    verified        INTEGER NOT NULL DEFAULT 0,-- fonte verificada (selo "fonte verificada")
    terms_reviewed  INTEGER NOT NULL DEFAULT 0,-- termos de uso e robots revisados
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS lots (
    id               INTEGER PRIMARY KEY,
    source_id        TEXT NOT NULL REFERENCES sources(id),
    external_id      TEXT NOT NULL,            -- id do lote na fonte
    category         TEXT NOT NULL CHECK (category IN ('imovel','veiculo')),
    title            TEXT,
    url              TEXT NOT NULL,            -- link da fonte (sempre guardado)
    leiloeiro        TEXT,
    comitente        TEXT,                     -- quem vende: banco, tribunal, órgão
    sale_type        TEXT,                     -- modalidade: venda direta, leilão SFI, judicial...
    praca            INTEGER,                  -- 1ª ou 2ª praça, quando houver
    auction_date     TEXT,                     -- ISO 8601
    appraisal_value  REAL,                     -- valor de avaliação
    min_bid          REAL,                     -- lance mínimo / preço atual
    discount_pct     REAL,                     -- calculado: 1 - min_bid/appraisal
    city             TEXT,
    uf               TEXT,
    neighborhood     TEXT,
    address          TEXT,
    description      TEXT,                     -- texto bruto do edital/anúncio
    photo_url        TEXT,                     -- foto principal (link da fonte)
    lot_count        INTEGER,                  -- >0 quando o registro é um edital com vários lotes
    distance_km      REAL,                     -- linha reta até a cidade-base (geo.py)
    in_area          INTEGER,                  -- 1 se atende ao parâmetro geográfico do cliente
    status           TEXT NOT NULL DEFAULT 'aberto', -- aberto | encerrado | removido
    content_hash     TEXT,                     -- detecta mudança no anúncio
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    closed_at        TEXT,
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_lots_uf_cat ON lots (uf, category, status);
CREATE INDEX IF NOT EXISTS idx_lots_discount ON lots (discount_pct);

CREATE TABLE IF NOT EXISTS lot_imovel (
    lot_id              INTEGER PRIMARY KEY REFERENCES lots(id) ON DELETE CASCADE,
    property_type       TEXT,      -- casa, apartamento, terreno, comercial...
    area_total_m2       REAL,
    area_privativa_m2   REAL,
    bedrooms            INTEGER,
    occupied            INTEGER,   -- 1 ocupado, 0 desocupado, NULL desconhecido
    judicial            INTEGER,   -- 1 judicial, 0 extrajudicial, NULL desconhecido
    accepts_financing   INTEGER,
    iptu_debt           REAL,
    condo_debt          REAL,
    encumbrances        TEXT       -- ônus/pendências na matrícula (texto)
);

CREATE TABLE IF NOT EXISTS lot_veiculo (
    lot_id          INTEGER PRIMARY KEY REFERENCES lots(id) ON DELETE CASCADE,
    brand           TEXT,
    model           TEXT,
    year            INTEGER,
    mileage_km      INTEGER,
    documentation   TEXT,
    origin          TEXT,      -- sinistro, financiamento, apreensão...
    runs            INTEGER,   -- roda ou não
    has_key         INTEGER,
    classification  TEXT       -- sucata | recuperavel | documentavel
);

-- Nota de conservação (A–D ou 'sem_info'), sempre com justificativa e evidência.
CREATE TABLE IF NOT EXISTS conservation (
    lot_id         INTEGER PRIMARY KEY REFERENCES lots(id) ON DELETE CASCADE,
    grade          TEXT NOT NULL CHECK (grade IN ('A','B','C','D','sem_info')),
    justification  TEXT NOT NULL,
    evidence       TEXT NOT NULL,      -- JSON: lista de trechos do edital/fotos
    method         TEXT NOT NULL,      -- 'ia:<modelo>' ou 'humano'
    content_hash   TEXT,               -- hash do conteúdo avaliado (reavaliar se mudar)
    graded_at      TEXT NOT NULL
);

-- Risco jurídico: sinais automáticos + revisão humana nos lotes de maior valor.
CREATE TABLE IF NOT EXISTS legal_risk (
    lot_id          INTEGER PRIMARY KEY REFERENCES lots(id) ON DELETE CASCADE,
    level           TEXT NOT NULL CHECK (level IN ('baixo','medio','alto','sem_info')),
    reasons         TEXT NOT NULL,      -- JSON: lista de motivos
    needs_review    INTEGER NOT NULL DEFAULT 0,
    human_reviewed  INTEGER NOT NULL DEFAULT 0,
    reviewer        TEXT,
    assessed_at     TEXT NOT NULL
);

-- Histórico append-only: cada vez que preço ou status muda, grava uma linha.
-- É a matéria-prima do produto "preço realizado".
CREATE TABLE IF NOT EXISTS lot_observations (
    id            INTEGER PRIMARY KEY,
    lot_id        INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    observed_at   TEXT NOT NULL,
    min_bid       REAL,
    appraisal_value REAL,
    status        TEXT NOT NULL,
    event         TEXT NOT NULL        -- novo | preco_alterado | reaberto | sumiu | arrematado
);
CREATE INDEX IF NOT EXISTS idx_obs_lot ON lot_observations (lot_id, observed_at);

-- Cada execução de captura por fonte: base do monitor diário de falhas.
CREATE TABLE IF NOT EXISTS capture_runs (
    id           INTEGER PRIMARY KEY,
    source_id    TEXT NOT NULL REFERENCES sources(id),
    scope        TEXT,                 -- ex.: UF capturada
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    ok           INTEGER NOT NULL DEFAULT 0,
    lots_seen    INTEGER DEFAULT 0,
    lots_new     INTEGER DEFAULT 0,
    lots_changed INTEGER DEFAULT 0,
    lots_closed  INTEGER DEFAULT 0,
    error        TEXT,
    alert        TEXT                  -- preenchido quando o volume cai de forma suspeita
);

-- Perfis de alerta dos assinantes (resumo diário).
CREATE TABLE IF NOT EXISTS profiles (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    email            TEXT,
    whatsapp         TEXT,
    category         TEXT CHECK (category IN ('imovel','veiculo')),
    ufs              TEXT,             -- CSV: 'ES,MG'
    cities           TEXT,             -- CSV opcional
    min_discount_pct REAL,
    max_price        REAL,
    max_risk         TEXT,             -- baixo | medio | alto
    min_grade        TEXT,             -- A | B | C | D
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL
);
