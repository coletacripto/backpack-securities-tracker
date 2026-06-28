# Solana Token Whale Alerts to Telegram

Bot em Python para monitorar transacoes on-chain de um token SPL na Solana e enviar alerta no Telegram quando o valor estimado passar de um limite em USD, por padrao `30000`.

## Backpack Securities tracker site

Uma primeira versao do site do tracker esta em `tracker_site/`. Ela renderiza um dashboard no estilo snapshot a partir de `tracker_site/data/snapshot.json`.

Rodar o site localmente:

```bash
./start_tracker_site.sh
```

Depois abra:

```text
http://127.0.0.1:8090/
```

Atualizar o snapshot uma vez manualmente:

```bash
python3 tracker_update.py --once
```

Rodar um processo que atualiza 1x por dia, por padrao as 14:30 no fuso `Europe/Rome`:

```bash
./start_tracker_daily_update.sh
```

Para mudar o horario:

```bash
TRACKER_UPDATE_HOUR=9 TRACKER_UPDATE_MINUTE=0 ./start_tracker_daily_update.sh
```

O updater usa fontes publicas para preencher o snapshot:

- Jupiter: volume 24h por acao, preco, variacao 24h, supply e holders.
- Blockworks: share do mercado Solana por issuer e volume cumulativo historico da Backpack.

## Hospedagem

O site pode ser hospedado como estatico na Vercel usando `tracker_site/` como output. O arquivo `vercel.json` ja aponta para essa pasta.

O workflow `.github/workflows/update-tracker.yml` atualiza `tracker_site/data/snapshot.json` todos os dias as 14:00 UTC e tambem pode ser rodado manualmente pelo botao `Run workflow` no GitHub Actions.

Fluxo em producao:

```text
GitHub Actions roda tracker_update.py
snapshot.json e atualizado
GitHub Actions faz commit
Vercel detecta o commit
site publica os dados novos
```

Os ativos monitorados ficam em `tracker_assets.json`. As chaves abaixo sao opcionais para fontes complementares, mas o tracker atual ja funciona com Jupiter e Blockworks sem chave paga:

```text
BIRDEYE_API_KEY=...
SOLSCAN_API_KEY=...
JUPITER_API_KEY=...
HELIUS_API_KEY=...
```

Quando configuradas, essas chaves podem servir como fallback/complemento para dados especificos.

## O que ele faz

- Filtra transferencias pelo `TOKEN_MINT`.
- Calcula valor em USD usando `FIXED_TOKEN_PRICE_USD` ou CoinGecko.
- Envia notificacao para Telegram.
- Roda em modo `webhook` ou `poll`.

## Melhor modo para token inteiro

Para acompanhar todas as transferencias de um token especifico, use `MODE=webhook` com um provedor como Helius Enhanced Webhooks. Monitorar um token inteiro apenas por RPC comum e polling nao e confiavel, porque muitas transferencias SPL nao aparecem quando se busca somente assinaturas pelo mint.

O modo `poll` serve para monitorar enderecos especificos, como wallets, pools, vaults ou token accounts.

## Configuracao

1. Crie um bot no Telegram com `@BotFather` e copie o token.
2. Descubra seu `TELEGRAM_CHAT_ID`.
3. Copie `.env.example` para `.env` e preencha os valores.

```bash
cp .env.example .env
```

## Rodar em modo webhook

```bash
MODE=webhook uvicorn solana_token_telegram_bot:app --host 0.0.0.0 --port 8080
```

No Helius, crie um webhook apontando para:

```text
https://SEU-DOMINIO/helius?secret=SEU_WEBHOOK_SECRET
```

Configure o webhook para receber transacoes dos enderecos relevantes do token/pools e enviar transacoes enriquecidas. O bot vai procurar `tokenTransfers` no payload e filtrar pelo mint configurado.

## Rodar em modo polling

```bash
MODE=poll python solana_token_telegram_bot.py
```

No `.env`, configure:

```text
HELIUS_API_KEY=...
WATCH_ADDRESSES=address1,address2,address3
```

## Observacoes importantes

- Para USD preciso em tokens novos/ilíquidos, prefira preencher `FIXED_TOKEN_PRICE_USD` ou adaptar o bot para buscar preco em DEX/pool especifica.
- Se o token tiver muitos holders e volume alto, webhooks/indexadores sao o caminho correto.
- O bot evita alertar a mesma assinatura duas vezes durante a execucao.
