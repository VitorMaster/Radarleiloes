# Checklist para ligar uma nova fonte

Antes de pôr qualquer captador em produção, confira cada item abaixo e registre o resultado na tabela `sources`.

- [ ] **Leiloeiro identificado**: nome e matrícula na Junta Comercial, ou órgão público responsável.
- [ ] **Termos de uso** lidos: a coleta automatizada é permitida? (`terms_reviewed = 1`)
- [ ] **robots.txt** respeitado.
- [ ] Existe **feed, CSV ou API oficial**? Se existir, use em vez de raspar HTML.
- [ ] Dá para **fazer parceria**? Muitos leiloeiros regionais preferem enviar a lista a ser raspados.
- [ ] **Limite de acesso**: no máximo 1 requisição por segundo, captura fora do horário comercial.
- [ ] **Dados pessoais**: não captar nome, CPF ou endereço do executado ou devedor.
- [ ] Cada lote guarda o **link para o edital** na fonte.
- [ ] Um teste com um arquivo real salvo em `tests/fixtures/`, para detectar mudança de formato.

## Mapa de fontes

| Camada | Fonte | Como captamos | Status |
|---|---|---|---|
| 1 | **Caixa** | CSV oficial por UF | ✅ ao vivo |
| 1 | **Portal Zuk** (Itaú, Santander, Bradesco, Sicoob, TJs) | listagem pública por UF + página de detalhe | ✅ ao vivo |
| 1 | **Mega Leilões** (judiciais e extrajudiciais) | listagem pública por UF (paginada) + detalhe | ✅ ao vivo |
| 2 | **Detran-BA** | página oficial de editais + Anexo I em PDF (um registro por veículo) | ✅ ao vivo |
| 4 | **ES Leilões** (Polícia Civil-ES, prefeituras, Findes) | eventos públicos, com lance vencedor dos lotes vendidos | ✅ ao vivo |
| 1 | Mega Leilões — veículos | já incluído na captura por UF (carros, motos, caminhões) | ✅ |
| 2 | Detran-MG (leilao.detran.mg.gov.br) | o site não respondeu a partir da nuvem | verificar |
| 1 | Seu Imóvel BB (Banco do Brasil) | a listagem exige token anti-robô | ⛔ não contornamos; pedir parceria/feed |
| 1 | Sodré Santoro, leilaoimovel.com.br | bloqueiam robôs (HTTP 403) | ⛔ pedir parceria |
| 1 | Superbid | API própria bloqueada (403) | ⛔ pedir acesso oficial |
| 2 | Detran-ES | editais de 2026 já realizados; acompanhar novos editais | próximo |
| 2 | Receita Federal (SLE) | portal exige navegador; editais em PDF no Serpro | próximo |
| 2 | Detran-MG | site não respondeu ao teste | verificar |
| 3 | TJES, TJMG, TJBA (leiloeiros nomeados) | diários da Justiça | próximo |
| 4 | Frazão e leiloeiros regionais | listagens públicas | próximo |
