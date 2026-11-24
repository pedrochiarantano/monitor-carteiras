# Painel de Controle · Etti Partners

Três seções no menu lateral: **Monitor de Carteiras** (posições da Comdinheiro), **Monitor de Fundos** (fundos, FIIs e listados, com dados públicos da CVM, ANBIMA, B3, Banco Central e Yahoo Finance) e **Monitor de Performance** (carteiras recomendadas por perfil de risco: resultado e composição CDI/Estratégico/Tático/Selection, índices, retorno x volatilidade, volatilidade móvel de 6 e 12 meses, drawdown e retornos mensais, a partir do Histórico de Cotação da Comdinheiro; `tools/performance.py` e `tools/atualizar_performance.py`). As carteiras recomendadas são portfólios do login dono delas na Comdinheiro: esse login vai nos segredos `COMDINHEIRO_PERF_USER` e `COMDINHEIRO_PERF_PASS`.

Site estático (GitHub Pages) com login por usuário e senha. **Os dados de clientes ficam criptografados** em `data/*.enc` (AES-256-GCM): sem um usuário e senha válidos, os arquivos são ilegíveis, mesmo com o repositório público.

## Como funciona

- `data/usuarios.json`: para cada usuário, a chave de dados cifrada com uma chave derivada da senha (PBKDF2-SHA256, 310 mil iterações). As senhas não ficam guardadas em lugar nenhum.
- `data/posicoes.enc`, `meta.enc`, `historico.enc`: posições da Comdinheiro, atualizadas todo dia útil às 07:17 pelo GitHub Actions.
- `data/duracoes.enc`: duration estimada dos fundos (carteira aberta na CVM + taxas ANBIMA), recalculada todo dia 16. Estimativas anteriores são mantidas quando um ativo não é recalculado.
- `data/fundos_cadastro.enc`: cadastro do Monitor de Fundos (ativos, classes, segmentos e benchmarks), editável na aba **Cadastro**.
- Ramo `base-fundos` (regravado a cada atualização, sem histórico, para o repositório não crescer): `fundos_base.enc` (cotas, índices, captação e proventos dos últimos ~5 anos), `fundos.enc` (métricas, ranking e status das fontes) e `fundos_series.enc` (séries semanais para os gráficos) e `performance.enc` (Monitor de Performance). Atualizado todo dia útil junto com as posições; quando alguém cadastra um ativo, o histórico dele é baixado na hora (`tools/atualizar_fundos.py --modo novos`).
- `data/config.enc`: IPS, carteira modelo, classificações, durations manuais e alertas reconhecidos. **Os jobs automáticos nunca alteram este arquivo**; só a página, quando alguém edita.

## Secrets do repositório (Settings → Secrets and variables → Actions)

| Nome | Conteúdo |
|---|---|
| `COMDINHEIRO_USER` | usuário da API Comdinheiro |
| `COMDINHEIRO_PASS` | senha da API Comdinheiro |
| `DATA_KEY` | chave de dados (32 bytes em base64) |

## Rodar manualmente

Actions → *Atualizar e publicar* → *Run workflow* → escolha `diario` (posições, fundos e performance), `posicoes`, `fundos`, `performance`, `duracoes`, `tudo` ou `so-publicar`.

## Editar pela página

Edições de IPS, modelo, classificação, durations manuais e o cadastro de fundos são gravadas no repositório pela própria página. Cada pessoa que edita precisa configurar, em **Acesso**, um token do GitHub (fine-grained, só este repositório, *Contents: Read and write*). Sem token, as alterações ficam só no navegador.

## Usuários

Administradores criam e removem usuários em **Acesso**. Remover um usuário tira o acesso pela página; para invalidar de vez o que ele já poderia ter decifrado, é preciso trocar a `DATA_KEY` e recifrar os arquivos.
