#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gerar_json_processo.py

Converte um ficheiro movimentos.md (gerado pela skill "movimentacao") num JSON
estruturado, pronto para ser publicado no site e consultado pelo cliente
através da página andamento.html.

O nome do ficheiro de saída NÃO é o número do processo em texto simples —
é um hash SHA-256 do número (normalizado). Isto evita que alguém descubra
o ficheiro de outro cliente só "adivinhando" ou enumerando números de
processo sequenciais no site público. A página andamento.html calcula o
mesmo hash no navegador a partir do número que o cliente digita.

USO:
    python3 gerar_json_processo.py "/caminho/para/movimentos.md" \
        --cliente "Nome Completo do Cliente" \
        --saida "/caminho/para/pasta/data/processos"

Depois de gerar, faça commit + push do .json resultante (e do andamento.html,
na primeira vez) para o repositório do site (ex.: marcioandrio.github.io).
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path
import json


def normalizar_numero(numero: str) -> str:
    """Remove tudo que não for letra/número e coloca em minúsculas.
    Tem de ser EXATAMENTE a mesma lógica usada no JS da página andamento.html."""
    return re.sub(r"[^a-zA-Z0-9]", "", numero or "").lower()


def hash_numero(numero: str) -> str:
    return hashlib.sha256(normalizar_numero(numero).encode("utf-8")).hexdigest()


def limpar_numero_processo(numero: str) -> str:
    """Alguns movimentos.md escrevem o número seguido de texto extra na mesma linha,
    ex.: '12710/26.8BELSB (Tribunal Administrativo de Círculo de Lisboa — ...)'.
    Ficamos só com o primeiro token (antes de espaço/parêntese), que é o número em si —
    senão o hash fica diferente do que o cliente digita no portal."""
    return (numero or "").split(" (")[0].split()[0] if numero else ""


def extrair_secao(texto: str, titulo: str) -> str:
    """Extrai o conteúdo entre '## titulo' e o próximo '## ' (ou fim do ficheiro)."""
    padrao = rf"##\s+{re.escape(titulo)}\s*\n(.*?)(?=\n##\s+|\Z)"
    m = re.search(padrao, texto, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def extrair_campo_topo(texto: str, rotulo: str) -> str:
    """Extrai campos do tipo '**Número do Processo:** valor' no topo do ficheiro."""
    padrao = rf"\*\*{re.escape(rotulo)}:\*\*\s*(.*)"
    m = re.search(padrao, texto)
    return m.group(1).strip() if m else ""


def extrair_titulo(texto: str) -> str:
    m = re.search(r"^#\s+Movimentações do Processo\s*-\s*(.+)$", texto, re.MULTILINE)
    return m.group(1).strip() if m else "Processo"


def extrair_info_basicas(bloco: str) -> dict:
    campos = ["Autor", "Réu", "Vara", "Valor da Causa", "Fase Atual"]
    dados = {}
    for campo in campos:
        m = re.search(rf"-\s*{re.escape(campo)}:\s*(.*)", bloco)
        dados[campo.lower().replace(" ", "_").replace("é", "e")] = (
            m.group(1).strip() if m else ""
        )
    return dados


def extrair_linha_do_tempo(bloco: str) -> list:
    """Cada evento começa em '**DD/MM/AAAA** - [Movimentação]'."""
    eventos = []
    partes = re.split(r"\n(?=\*\*\d{2}/\d{2}/\d{4}\*\*\s*-)", bloco.strip())
    for parte in partes:
        parte = parte.strip()
        if not parte:
            continue
        m = re.match(r"\*\*(\d{2}/\d{2}/\d{4})\*\*\s*-\s*(.+)", parte)
        if not m:
            continue
        data, movimentacao = m.group(1), m.group(2).strip()
        resumo_m = re.search(r"^>\s*(.+)$", parte, re.MULTILINE)
        docs_m = re.search(r"-\s*Documentos juntados:\s*(.*)", parte)
        prazos_m = re.search(r"-\s*Prazos:\s*(.*)", parte)
        eventos.append({
            "data": data,
            "movimentacao": movimentacao,
            "resumo": resumo_m.group(1).strip() if resumo_m else "",
            "documentos_juntados": docs_m.group(1).strip() if docs_m else "",
            "prazos": prazos_m.group(1).strip() if prazos_m else "",
        })
    return eventos


def extrair_lista(bloco: str) -> list:
    return [
        re.sub(r"^-+\s*", "", linha).strip()
        for linha in bloco.strip().splitlines()
        if linha.strip().startswith("-")
    ]


def extrair_prazos(bloco: str) -> dict:
    def pega(nivel_emoji, titulo):
        m = re.search(
            rf"{nivel_emoji}\s*\*?\*?{titulo}\*?\*?:?\s*\n(.*?)(?=\n🔴|\n🟠|\n🟡|\Z)",
            bloco,
            re.DOTALL,
        )
        return extrair_lista(m.group(1)) if m else []

    return {
        "urgente": pega("🔴", "Urgente"),
        "atencao": pega("🟠", "Atenção"),
        "monitorar": pega("🟡", "Monitorar"),
    }


def extrair_tabela(bloco: str, colunas: list) -> list:
    linhas = [l for l in bloco.strip().splitlines() if l.strip().startswith("|")]
    linhas = [l for l in linhas if not re.match(r"^\|\s*-+\s*\|", l)]
    resultado = []
    for l in linhas[1:] if linhas else []:
        celulas = [c.strip() for c in l.strip().strip("|").split("|")]
        if len(celulas) >= len(colunas) and not all(c == "" for c in celulas):
            resultado.append(dict(zip(colunas, celulas)))
    return resultado


def extrair_atualizado_em(texto: str) -> str:
    m = re.search(r"\*Atualizado em (.+?)\*", texto)
    return m.group(1).strip() if m else ""


def converter(md_path: Path, cliente: str) -> dict:
    texto = md_path.read_text(encoding="utf-8")

    numero_processo = limpar_numero_processo(extrair_campo_topo(texto, "Número do Processo"))
    if not numero_processo:
        print("⚠️  Não encontrei 'Número do Processo' no ficheiro. "
              "O JSON será gerado mesmo assim, mas confirme manualmente "
              "e reprocesse se necessário.", file=sys.stderr)

    dados = {
        "numero_processo": numero_processo,
        "cliente": cliente,
        "tipo_acao": extrair_titulo(texto),
        "data_analise": extrair_campo_topo(texto, "Data da Análise"),
        "informacoes_basicas": extrair_info_basicas(
            extrair_secao(texto, "Informações Básicas")
        ),
        "linha_do_tempo": extrair_linha_do_tempo(
            extrair_secao(texto, "Linha do Tempo de Movimentações")
        ),
        "novas_movimentacoes": extrair_lista(
            extrair_secao(texto, "Novas Movimentações")
        ),
        "prazos": extrair_prazos(
            extrair_secao(texto, "Prazos e Diligências Pendentes")
        ),
        "proximas_acoes": extrair_lista(
            extrair_secao(texto, "Próximas Ações Sugeridas")
        ),
        "evolucao": extrair_tabela(
            extrair_secao(texto, "Evolução do Processo"),
            ["data", "evento", "fase", "impacto"],
        ),
        "observacoes": extrair_lista(
            extrair_secao(texto, "Observações Importantes")
        ),
        "atualizado_em": extrair_atualizado_em(texto),
    }
    return dados


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("movimentos_md", type=Path, help="Caminho para o movimentos.md")
    ap.add_argument("--cliente", required=True, help="Nome completo do cliente, exatamente como será digitado por ele na conferência (usado para liberar o acesso aos dados)")
    ap.add_argument("--saida", type=Path, default=Path("."), help="Pasta onde salvar o JSON (ex.: data/processos do site)")
    args = ap.parse_args()

    if not args.movimentos_md.exists():
        print(f"Ficheiro não encontrado: {args.movimentos_md}", file=sys.stderr)
        sys.exit(1)

    dados = converter(args.movimentos_md, args.cliente)

    if not dados["numero_processo"]:
        print("❌ Não é possível gerar o JSON sem o Número do Processo "
              "(é a chave usada para o hash do ficheiro). Preencha o campo "
              "'Número do Processo' no movimentos.md e rode novamente.", file=sys.stderr)
        sys.exit(1)

    h = hash_numero(dados["numero_processo"])
    args.saida.mkdir(parents=True, exist_ok=True)
    destino = args.saida / f"{h}.json"
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ JSON gerado: {destino}")
    print(f"   Número do processo: {dados['numero_processo']}")
    print(f"   Cliente (nome completo p/ conferência): {dados['cliente']}")
    print(f"   Hash (nome do ficheiro): {h}")
    print()
    print("Link para enviar ao cliente (troque pelo domínio real do site ma-advocacia):")
    print(f"   https://SEU-DOMINIO/upload/andamento.html?numero={dados['numero_processo']}")


if __name__ == "__main__":
    main()
