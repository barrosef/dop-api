"""Casos de uso do BFF — a lógica que REST e gRPC compartilham.

Aqui mora tudo o que não é transporte: ler o contexto, falar com o núcleo,
traduzir o vocabulário. Os routers e os servicers são adaptadores finos por
cima destas funções. Duplicar qualquer coisa daqui numa das pontas é como as
duas versões começam a divergir.
"""
