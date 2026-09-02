"""The BFF's use cases — the logic REST and gRPC share.

Everything that is not transport lives here: reading the context, talking to the
core, translating the vocabulary. The routers and the servicers are thin adapters
on top of these functions. Duplicating anything from here at one of the ends is
how the two versions start diverging.
"""
