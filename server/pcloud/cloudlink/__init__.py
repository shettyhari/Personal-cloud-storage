"""Cloud link: connects this agent outbound to the hosted Personal Cloud dashboard (Supabase).

The agent never accepts inbound connections for this. It signs in to Supabase as its own dedicated
"server" account, keeps a heartbeat row up to date, and listens on a private Realtime channel
`server:<id>` that only its owner (signed in with 2FA) and this agent may join. Requests arriving on
that channel are dispatched in-process to the normal REST API (see tunnel.py).
"""
