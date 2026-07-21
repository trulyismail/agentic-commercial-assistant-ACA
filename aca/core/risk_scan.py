"""
Scanner de risques contractuels — nœud déterministe (RegEx, aucun appel LLM/API).

Inspiré du "Trapdoor Risk Engine" décrit dans les deux PDF "ACAM v2 Blueprint" reçus le 2026-07-16
(cf. docs/ACAM_roadmap.md §13) : avant toute rédaction, on cherche dans le texte entrant (corps de
l'e-mail + pièces jointes) des formulations qui engagent lourdement l'entreprise (responsabilité
illimitée, pénalités, clauses de non-concurrence...) — le genre de clause qu'un commercial peut
laisser passer dans un cahier des charges de 20 pages, mais qu'une regex ne rate jamais et qui
mérite une relecture humaine/juridique avant tout engagement écrit. Volontairement déterministe
(pas de LLM) : rapide, gratuit, et le résultat est identique à chaque exécution sur le même texte —
contrairement à `search_knowledge_base_semantic()`, il n'y a ici aucune notion de score de
confiance à calibrer.
"""
import re
import unicodedata

# (étiquette affichée à l'humain, motif à chercher dans le texte normalisé — minuscules, sans accents)
RISK_PATTERNS: list[tuple[str, str]] = [
    ("Responsabilité illimitée", r"responsabilite\s+illimitee|unlimited\s+liability"),
    ("Dommages et intérêts / liquidated damages", r"dommages[\s-]+et[\s-]+interets|dommages[\s-]+interets|liquidated\s+damages"),
    ("Pénalités de retard", r"penalites?\s+de\s+retard|penalty\s+for\s+delay|late\s+penalt"),
    ("Astreinte financière", r"astreinte"),
    ("Garantie bancaire à première demande", r"garantie\s+bancaire|bank\s+guarantee"),
    ("Clause de non-concurrence", r"non[\s-]+concurrence|non[\s-]+compete"),
    ("Résiliation unilatérale / immédiate", r"resiliation\s+(unilaterale|immediate)|immediate\s+termination"),
    ("Exclusivité contractuelle", r"exclusivite\s+contractuelle|exclusivity\s+clause"),
]

_COMPILED = [(label, re.compile(pattern, re.IGNORECASE)) for label, pattern in RISK_PATTERNS]


def _normalize(text: str) -> str:
    """Minuscules + accents retirés (« pénalité » -> « penalite ») pour un matching robuste à la
    saisie réelle (accents parfois absents dans un e-mail tapé vite, variantes FR/EN)."""
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return stripped.lower()


def scan_risks(text: str) -> list[str]:
    """
    Renvoie la liste des libellés de risques détectés dans `text` (corps de l'e-mail + pièces
    jointes concaténés), dans l'ordre de `RISK_PATTERNS`, sans doublon. Liste vide si `text` est
    vide ou si aucun motif ne correspond.
    """
    if not text:
        return []
    normalized = _normalize(text)
    return [label for label, pattern in _COMPILED if pattern.search(normalized)]
