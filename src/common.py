"""Petits outils partagés par les scripts du pipeline."""

import os


def job_summary(markdown: str) -> None:
    """Ajoute un bloc au résumé de l'exécution GitHub Actions (onglet Actions → l'exécution → Summary).
    Sans effet en local : la variable GITHUB_STEP_SUMMARY n'existe que sur GitHub Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown.rstrip() + "\n\n")


def gh_warning(message: str) -> None:
    """Avertissement visible dans l'onglet Actions (annotation jaune), sans faire échouer le job."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::{message}")
