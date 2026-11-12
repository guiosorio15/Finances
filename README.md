# raiz.

Finanças pessoais, com raízes. Uma aplicação local para perceber o salário, dar destino ao dinheiro e construir o futuro — sem cloud, sem contas, sem ruído.

O `raiz` corre em `localhost` e guarda tudo em SQLite, na pasta `data/`. Foi desenhado para um utilizador: tu. A cor principal é verde escuro; o fundo é creme; a tipografia é Fraunces + Inter; os gráficos são SVG/CSS, sem bibliotecas pesadas.

## O que já faz

- **Importa recibos de vencimento em PDF** e lê automaticamente:
  - mês, empregador, colaborador, NIF, Segurança Social, IBAN;
  - ordenado base, subsídios de férias e Natal, outras remunerações;
  - dias e valor diário do cartão refeição;
  - IRS, Segurança Social, total de descontos;
  - líquido em conta, cartão refeição e total recebido.
- **Valida a aritmética** do recibo e mostra avisos antes de guardar.
- **Cria dois movimentos separados**: líquido em conta e cartão refeição. O cartão não é tratado como cash.
- **Dashboard mensal** com rendimento, despesas, futuro, por atribuir, fluxo de caixa, análise por categoria, alocação 50/30/20 e sugestões.
- **Movimentos** com filtros, pesquisa, criação, edição e eliminação.
- **Orçamento por envelopes** (zero-based) com valores planeados, gastos e restantes.
- **Subscrições** com valor mensal/anual normalizado, próxima cobrança e ação “marcar paga”.
- **Objetivos** com progresso, reforço mensal e projeção de chegada.
- **Definições** de perfil, alocação, contas e categorias.
- **Backup completo**, exportação JSON e CSV.
- **Tudo local**: sem login, telemetria, analytics ou chamadas externas.

## Correr

Requisitos: Python 3.11+ e [uv](https://docs.astral.sh/uv/). Opcional: `pdftotext` (poppler-utils) para uma leitura de PDF ainda melhor; o `pdfplumber` serve de fallback.

```bash
cd raiz
./run.sh
```

Abre `http://127.0.0.1:8000`. Para desenvolvimento com reload:

```bash
RAIZ_RELOAD=1 ./run.sh
```

## Primeiro recibo

1. Abre **Recibos → Importar recibo**.
2. Arrasta o PDF ou escolhe o ficheiro.
3. Revê os campos. A leitura automática nunca guarda nada sem confirmação.
4. Confirma. O raiz cria o rendimento em conta e o cartão refeição como movimentos separados.

O parser está em `app/parsers/payslip.py`. Foi feito para o formato do recibo que usaste, mas usa rótulos comuns dos recibos portugueses. Se o teu próximo recibo mudar de layout, o ecrã de revisão permite corrigir tudo à mão.

> PDFs digitalizados (imagens) não têm texto selecionável. Nesse caso, pede a versão digital ao empregador ou adiciona o recibo manualmente pelos movimentos.

## Stack

- **FastAPI** para o servidor local e as rotas.
- **Jinja2** para HTML server-rendered, rápido e sem build step.
- **SQLite** para os dados, com WAL e backups.
- **pdfplumber + pdftotext** para leitura de PDF.
- **HTML/CSS/JS vanilla** no frontend. Sem React, sem Tailwind, sem bundler.
- **SVG/CSS** para gráficos. Sem Chart.js.

Porquê: uma app pessoal, local e de longa duração beneficia de poucas peças móveis. O backend trata do PDF e da base de dados; o frontend é HTML com melhorias progressivas. O resultado arranca em segundos e é fácil de manter.

## Estrutura

```
raiz/
  app/
    main.py              # rotas e composição das páginas
    db.py                # schema, seed e ligação SQLite
    services.py          # consultas e ações de domínio
    utils.py             # dinheiro, datas, meses
    icons.py             # ícones SVG inline
    parsers/payslip.py   # leitura e validação do recibo
    templates/           # Jinja
    static/
      css/app.css        # design system
      js/app.js          # modais, upload, resumo em direto
      fonts/             # Fraunces + Inter
  data/                  # NÃO versionar: base de dados + PDFs
  run.sh
```

## Privacidade e backups

- O servidor liga-se apenas a `127.0.0.1`.
- Não há autenticação porque não há exposição à rede. Se um dia quiseres aceder de outro dispositivo, coloca isto atrás de uma VPN e adiciona autenticação.
- `data/` contém dados pessoais. Está no `.gitignore`.
- **Definições → Backup completo** descarrega um zip com a base de dados e os PDFs. Guarda-o num local seguro.
- Para recomeçar, usa a zona de perigo nas definições. As contas, categorias e definições mantêm-se.

## Decisões de design

- **Verde escuro (`#173528`) + creme (`#F4ECDE`) + ocre (`#C08A3E`) + sálvia (`#7E9B7A`)**. O verde dá profundidade; o creme reduz a ansiedade de “folha de cálculo”; o ocre marca o que é importante sem gritar.
- **Fraunces** para títulos e números grandes; **Inter** para interface e valores tabulares.
- Cartões com bordas finas, sombras quase inexistentes e muito espaço. O dinheiro é o protagonista, não os botões.
- Gráficos sem ruído: um gráfico de barras para entradas/saídas, uma barra de alocação, anéis de progresso. Sem dougnuts com 12 cores.
- O cartão refeição é mostrado sempre separado do cash. É dinheiro, mas com destino.
- Estados vazios com uma ação clara, em vez de gráficos a zero.

## Próximos passos possíveis

- Regra de alocação pessoal e simulador “e se…”.
- Importação de extratos bancários em CSV.
- Despesas fixas recorrentes para além de subscrições.
- OCR para recibos digitalizados.
- Património líquido e evolução mensal.
- PPR/ETF: acompanhar contribuições, não dar conselhos.
- Autenticação local opcional e cifra da base de dados.

> O raiz é uma ferramenta de organização. Não é aconselhamento financeiro, fiscal ou de investimento.
