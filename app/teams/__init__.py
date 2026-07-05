# app/teams/
#
# Microsoft 365 Agents SDK bootstrapping for the Teams channel.
# Mirrors the role app/checkpointer.py and app/database.py play for
# their own concerns — connect/get accessor functions, called once
# from main.py's lifespan, storing what's needed on app.state.
#
# NOT a repository/service/api layer itself — those live in their
# usual locations (app/repository/teams_conversation_repository.py,
# app/services/teams_service.py, app/api/teams.py) per this
# project's existing convention. This package is purely SDK
# plumbing, kept separate so that convention isn't muddied by
# SDK-specific setup code.
#
# STATUS: see rfp_analyzer_teams_integration.md — several pieces
# here are still marked UNCONFIRMED pending real Terraform outputs,
# Ocelot config, and hands-on testing against a real Azure Bot
# resource. Do not treat this as a finished, deployed integration.