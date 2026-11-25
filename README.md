# 📡 Radar de Leilões

> O radar vende **decisão**, não listagem: transforma editais dispersos em lotes comparáveis,
> já com desconto real, estado de conservação e risco jurídico avaliados.
> Recorte: imóveis e veículos em **ES, MG e BA**.

Este repositório tem o MVP descrito no *Plano estratégico e MVP* (24/11/2026). Ele cobre a
**Fase 1, "Base"** (esquema de dados, captura diária e primeira fonte no ar) e já traz a estrutura
das fases 2 e 3 (nota de conservação por IA, risco jurídico e resumo diário).

## O que já funciona

| Parte | Onde | Situação |
|---|---|---|
| Esquema de dados (campos comuns + imóvel + veículo + conservação + risco) | `radar/schema.sql` | ✅ |
| **Histórico append-only** de preço e status desde o 1º dia | tabela `lot_observations` | ✅ |
| Fonte 1: **Caixa**, pelo CSV oficial por UF (sem raspagem de HTML) | `radar/sources/caixa.py` | ✅ parser testado; download depende da rede do servidor |
| Captura diária: novos, alterados, **encerrados** (sumiram da fonte) | `radar/pipeline.py` | ✅ |
| Monitor de fontes: falha, zero lotes, **queda brusca de volume** | `radar health`, página `/fontes` | ✅ |
| Risco jurídico: sinais automáticos do edital + fila de revisão do advogado | `radar/risk.py`, `radar review` | ✅ (critérios iniciais, a calibrar) |
| Nota de conservação A–D por IA, com justificativa, evidências e "sem informação suficiente" | `radar/conservation.py` | ✅ (precisa de `ANTHROPIC_API_KEY`) |
| Painel web com filtros por desconto, preço, risco e conservação | `radar serve` → http://127.0.0.1:8000 | ✅ |
| Perfis de alerta + resumo diário por e-mail e texto para WhatsApp | `/perfis`, `radar digest` | ✅ (envio por SMTP opcional) |
| API JSON (embrião do feed B2B) | `GET /api/lotes` | ✅ (falta a chave de acesso) |

## Como rodar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

radar capture                 # baixa a Caixa para ES, MG e BA
radar capture --uf ES --file tests/fixtures/caixa_ES.csv   # ou a partir de um arquivo local
export ANTHROPIC_API_KEY=...  # opcional: nota de conservação
radar grade --limit 20        # avalia primeiro os 20 lotes de maior desconto
radar serve                   # abre o painel
radar digest                  # gera o resumo diário em data/digests/ (ou envia por SMTP)
radar daily                   # rotina completa, para o cron
pytest                        # testes
```

Cron sugerido (todo dia às 6h): `0 6 * * * cd /caminho && .venv/bin/radar daily >> data/daily.log 2>&1`

Variáveis de ambiente: `RADAR_DB` (padrão `data/radar.db`), `RADAR_MODEL`,
`ANTHROPIC_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` e `SMTP_FROM`.

## Para adicionar uma fonte

1. Crie `radar/sources/<nome>.py` com uma classe `Source` que implementa `fetch(scope)` e devolve objetos `Lot`.
2. Registre a classe em `radar/sources/__init__.py`.
3. Antes de ligar a fonte, confira o checklist em [`docs/fontes.md`](docs/fontes.md).

---

## Sugestões para aperfeiçoar o plano

Estas ideias surgiram enquanto eu transformava o plano em código. Estão em ordem de impacto.

### 1. Comece a guardar o "preço realizado" agora, mesmo antes de vender esse produto
Concordo com a sua aposta final, e por isso ela já está no esquema. A tabela `lot_observations` é
append-only: grava cada queda de preço, cada reabertura e o dia em que o lote **sumiu** da fonte.
Na Caixa, um lote que sai da lista quase sempre foi vendido. Um lote que volta com preço menor
indica que a praça anterior fracassou. Com 6 a 12 meses de histórico, o radar consegue responder:
*"imóveis em Vila Velha com desconto acima de 40% costumam ser vendidos em quantos dias?"*.
Hoje quase ninguém tem essa resposta. **Ligue o `radar daily` no cron o quanto antes**, porque
cada dia sem captura é um dia de histórico perdido.

### 2. Use a Caixa como fonte nº 1
O plano põe a Caixa na camada 1, com captura "média". Na prática ela é a fonte mais fácil:
publica um **CSV oficial por UF** com preço, avaliação, desconto, modalidade e link. Não precisa
raspar HTML nem enfrentar bloqueio de robô, e o risco de termos de uso é baixo. Com ela, o
**Portão 1 fica praticamente cumprido**. Prefira fontes assim (arquivo ou feed oficial) a portais
que bloqueiam robôs.

### 3. Trate risco jurídico em duas camadas
- **Automática (já feita):** sinais no texto, como *ocupado*, *penhora*, *usufruto*, *ação judicial* e dívidas acima de 10% do lance.
- **Humana:** todo lote de risco "alto" ou com lance a partir de R$ 300 mil cai numa fila de revisão. A revisão do advogado nunca é sobrescrita pela captura seguinte.

Também criei o nível **"sem informação"** para o risco, além de A–D/"sem info" na conservação.
Um imóvel da Caixa sem nenhuma menção a ocupação **não** é risco baixo, é risco desconhecido.
Mostrar isso com honestidade é o que diferencia o radar de um listão.

### 4. A nota por IA precisa ser barata e verificável
- Só reavalia quando o conteúdo do lote muda (`content_hash`) e começa pelos maiores descontos.
- Sempre devolve **justificativa e trechos literais** do edital como evidência.
- A descrição da Caixa no CSV é curta: área, quartos, vagas. Por isso, muitos lotes vão receber
  "sem informação suficiente", e isso está certo. O ganho real da IA vem quando o radar ler o
  **PDF do edital e as fotos** da página de detalhe. Esse deve ser o próximo passo da Fase 2.

### 5. Ajustes no produto e na monetização
- **Desconto real ≠ desconto anunciado.** Calcule também o "desconto líquido": lance mínimo + ITBI
  (cerca de 2–3%) + registro + dívidas de IPTU e condomínio + custo estimado de desocupação. É
  essa conta que o investidor faz de cabeça, e o radar pode fazê-la por ele.
- **Pré-venda antes de mais fontes (Portão 3):** use o próprio resumo diário como produto de teste.
  Mande grátis por 2 semanas para 30 investidores de ES/MG/BA e depois ofereça a assinatura.
- **Relatório de risco por lote** é o produto de maior margem e o que mais aproveita a sua
  vantagem jurídica. Vale testá-lo antes do B2B.
- Mantenha a regra do plano: **nunca** cobrar comissão sobre arremate nem intermediar lance. Isso
  já está no rodapé de todas as páginas e e-mails.

### 6. LGPD e confiança
- O esquema **não tem** campo para nome ou CPF do executado, e isso é de propósito.
- "Alertas por nome de executado" (um dos horizontes do plano) só deve existir para **clientes do
  escritório**, com base legal definida e fora do produto público.
- Cada lote guarda o link da fonte oficial e o selo "fonte verificada". Só entram fontes com
  leiloeiro identificado, o que protege o assinante contra sites falsos de leilão.

### 7. Ponto pessoal antes de constituir a empresa
O plano já lista o seu vínculo como servidor municipal. Confirme no estatuto do município se é
permitido participar como sócio ou administrador. Se houver restrição, uma saída comum é ser
sócio quotista sem administração, mas isso precisa ser checado no caso concreto.

## Próximos passos (alinhados aos portões)

- [ ] **Portão 1:** rodar `radar daily` num servidor (pode ser uma VPS barata) e acumular histórico.
- [ ] Ler a página de detalhe da Caixa: fotos, matrícula, se aceita financiamento e regras de despesas.
- [ ] Mais 4 fontes de imóveis em ES/MG (leiloeiros judiciais do TJES/TJMG e portais da camada 1).
- [ ] **Portão 2:** nota de conservação com fotos e PDF do edital.
- [ ] Cálculo do desconto líquido.
- [ ] Envio do resumo por WhatsApp (API oficial do WhatsApp Business).
- [ ] **Portão 3:** página de pré-venda e 10 pessoas dispostas a pagar.
- [ ] Veículos: Detran ES/MG/BA, Receita Federal e PRF.
