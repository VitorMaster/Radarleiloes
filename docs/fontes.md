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

## Mapa de fontes (do plano)

| Camada | Fontes | Status |
|---|---|---|
| 1. Grande volume | **Caixa (CSV oficial)** ✅, bancos privados, Superbid, Sodré Santoro, Mega, Zuk, Frazão e Franco | Caixa no ar |
| 2. Setor público | Receita Federal, Detrans, PRF, prefeituras ES/MG/BA | — |
| 3. Judicial | Leiloeiros nomeados pelos TJs e diários da Justiça | — |
| 4. Regionais | Leiloeiros locais ES/MG/BA | — |
