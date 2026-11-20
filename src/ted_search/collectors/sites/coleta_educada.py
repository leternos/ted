"""GET educado para os adaptadores de sites pequenos (DF, SE, BA): User-Agent identificável, intervalo fixo entre requisições
e parada limpa quando o site recusa (401/403/429 ou captcha). Nunca contorna: depois do primeiro bloqueio nenhuma outra
requisição é feita naquela execução.
"""

from __future__ import annotations

import asyncio
from typing import Any

USER_AGENT = "TEDSearch-OAB-Backfill/1.0 (pesquisa de jurisprudencia; coleta educada; 1 conexao, 1 req/2 s)"
PAUSA = 2.0  # segundos entre requisições (a pausa fica dentro do semáforo, com 1 conexão)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}
MARCAS_CAPTCHA = ("g-recaptcha", "h-captcha", "hcaptcha", "cf-challenge", "captcha-delivery", "Just a moment...")


class BloqueioSite(RuntimeError):
    """O site recusou (401/403/429) ou pediu captcha: a coleta para, sem insistir e sem contornar."""


_bloqueio: list[str] = []


def reiniciar() -> None:
    _bloqueio.clear()


async def baixar_bytes(client: Any, sem: asyncio.Semaphore, url: str, marca: bytes | None = None, tentativas: int = 4, pausa: float | None = None) -> bytes:
    """GET com repetição só para erro transitório (5xx, rede, resposta cortada). 401/403/429/captcha levantam ``BloqueioSite``."""
    espera = PAUSA if pausa is None else pausa
    erro: Exception | None = None
    for n in range(tentativas):
        if _bloqueio:
            raise BloqueioSite(_bloqueio[0])
        try:
            async with sem:
                resp = await client.get(url, headers=HEADERS)
                await asyncio.sleep(espera)
            status = getattr(resp, "status_code", 200)
            if status in (401, 403, 429):
                _bloqueio.append(f"HTTP {status} em {url}")
                raise BloqueioSite(_bloqueio[0])
            resp.raise_for_status()
            corpo = resp.content
            amostra = corpo[:200000].decode("utf-8", "ignore")
            if any(m in amostra for m in MARCAS_CAPTCHA) and (marca is None or marca not in corpo):
                _bloqueio.append(f"captcha em {url}")
                raise BloqueioSite(_bloqueio[0])
            if marca is None or marca in corpo:
                return corpo
            erro = ValueError("resposta fora do padrão do site")
        except BloqueioSite:
            raise
        except Exception as exc:  # noqa: BLE001
            erro = exc
        await asyncio.sleep(espera * (n + 2))
    raise RuntimeError(f"falha em {url}: {erro}")


async def baixar_texto(client: Any, sem: asyncio.Semaphore, url: str, marca: str | None = None, **kw: Any) -> str:
    corpo = await baixar_bytes(client, sem, url, marca.encode() if marca else None, **kw)
    return corpo.decode("utf-8", "replace")
