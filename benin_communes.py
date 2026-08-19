"""
Données administratives du Bénin : 12 départements et leurs 77 communes.

Utilisé pour le ciblage géographique fin des campagnes Pubwek.
Import dans main.py :  from benin_communes import DEPARTEMENTS_COMMUNES
"""

DEPARTEMENTS_COMMUNES = {
    "Alibori": [
        "Banikoara", "Gogounou", "Kandi", "Karimama", "Malanville", "Ségbana"
    ],
    "Atacora": [
        "Boukoumbé", "Cobly", "Kérou", "Kouandé", "Matéri",
        "Natitingou", "Péhunco", "Tanguiéta", "Toucountouna"
    ],
    "Atlantique": [
        "Abomey-Calavi", "Allada", "Kpomassè", "Ouidah",
        "Sô-Ava", "Toffo", "Tori-Bossito", "Zè"
    ],
    "Borgou": [
        "Bembéréké", "Kalalé", "N'Dali", "Nikki", "Parakou",
        "Pèrèrè", "Sinendé", "Tchaourou"
    ],
    "Collines": [
        "Bantè", "Dassa-Zoumè", "Glazoué", "Ouèssè", "Savalou", "Savè"
    ],
    "Couffo": [
        "Aplahoué", "Djakotomey", "Dogbo", "Klouékanmè", "Lalo", "Toviklin"
    ],
    "Donga": [
        "Bassila", "Copargo", "Djougou", "Ouaké"
    ],
    "Littoral": [
        "Cotonou"
    ],
    "Mono": [
        "Athiémé", "Bopa", "Comè", "Grand-Popo", "Houéyogbé", "Lokossa"
    ],
    "Ouémé": [
        "Adjarra", "Adjohoun", "Aguégués", "Akpro-Missérété",
        "Avrankou", "Bonou", "Dangbo", "Porto-Novo", "Sèmè-Kpodji"
    ],
    "Plateau": [
        "Adja-Ouèrè", "Ifangni", "Kétou", "Pobè", "Sakété"
    ],
    "Zou": [
        "Abomey", "Agbangnizoun", "Bohicon", "Covè", "Djidja",
        "Ouinhi", "Za-Kpota", "Zagnanado", "Zogbodomey"
    ],
}


def toutes_les_communes():
    """Retourne la liste à plat de toutes les communes valides (pour validation serveur)."""
    communes = []
    for liste in DEPARTEMENTS_COMMUNES.values():
        communes.extend(liste)
    return communes


def commune_appartient_a(commune, departement):
    """Vérifie qu'une commune donnée appartient bien au département indiqué."""
    return commune in DEPARTEMENTS_COMMUNES.get(departement, [])