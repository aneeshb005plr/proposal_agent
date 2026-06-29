# app/agent/context.py
#
# Run-scoped context — confirmed current pattern: "Static runtime
# context represents immutable data like user metadata, tools, and
# database connections that are passed to an application at the
# start of a run via the context argument to invoke/stream."
#
# Used instead of closures or globals for injecting the database
# connection into nodes — every node that needs db access receives
# it via runtime.context.db, not by importing a module-level
# connection directly.

from dataclasses import dataclass

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database



@dataclass
class AgentContext:
    db: AsyncDatabase
    sync_db: Database
