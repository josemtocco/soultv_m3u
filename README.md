# Soul TV → M3U automático

Gerador de playlist M3U baseado na plataforma oficial da Soul TV.

Fonte:
https://www.soultv.com.br/

## O que o projeto faz

- consulta a API de canais da Soul TV;
- captura nome, ID, logo, categoria e URL de transmissão;
- organiza os canais por `group-title`;
- testa os streams HLS antes de adicioná-los;
- remove automaticamente os streams que falharem no teste;
- gera `tvg-id`, `tvg-name` e `tvg-logo` quando disponíveis;
- atualiza automaticamente a cada 6 horas;
- mantém os arquivos gerados no diretório principal do repositório.

## Arquivos na raiz

- `gerar_m3u.py` — gerador principal;
- `requirements.txt` — dependência Python;
- `soultv.m3u` — playlist pronta para usar;
- `canais_soultv.json` — canais aprovados;
- `status_soultv.json` — relatório do último teste;
- `soultv_api.json` — resposta bruta da API usada na última atualização.

O workflow fica em `.github/workflows/atualizar.yml`, pois o GitHub exige esse local para executar o GitHub Actions.

## Atualização a cada 6 horas

O workflow executa às 00:00, 06:00, 12:00 e 18:00 UTC.

No horário de Brasília (UTC-3), isso corresponde aproximadamente a:
- 21:00
- 03:00
- 09:00
- 15:00

O GitHub pode atrasar alguns minutos a execução do cron.

## Uso no SS IPTV

Depois de colocar o projeto no GitHub, use:

`https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPOSITORIO/main/soultv.m3u`

Substitua `SEU_USUARIO/SEU_REPOSITORIO` pelos dados do seu repositório.

## Execução manual

Também é possível executar localmente:

```bash
pip install -r requirements.txt
python gerar_m3u.py
```

## Observação sobre a fonte

A Soul TV informa que sua plataforma oferece canais ao vivo e conteúdo licenciado. O projeto apenas monta a playlist a partir dos dados de transmissão disponibilizados pela plataforma e remove links que não passam no teste de disponibilidade.
