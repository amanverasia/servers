import logging
from pathlib import Path
from typing import Sequence
from mcp.server import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from mcp.types import (
    ClientCapabilities,
    TextContent,
    Tool,
    ListRootsResult,
    RootsCapability,
)
from enum import Enum
import git
from pydantic import BaseModel

class GitStatus(BaseModel):
    repo_path: str

class GitDiffUnstaged(BaseModel):
    repo_path: str

class GitDiffStaged(BaseModel):
    repo_path: str

class GitCommit(BaseModel):
    repo_path: str
    message: str

class GitAdd(BaseModel):
    repo_path: str
    files: list[str]

class GitReset(BaseModel):
    repo_path: str

class GitLog(BaseModel):
    repo_path: str
    max_count: int = 10

class GitTools(str, Enum):
    STATUS = "git_status"
    DIFF_UNSTAGED = "git_diff_unstaged"
    DIFF_STAGED = "git_diff_staged"
    COMMIT = "git_commit"
    ADD = "git_add"
    RESET = "git_reset"
    LOG = "git_log"

def git_status(repo: git.Repo) -> str:
    return repo.git.status()

def git_diff_unstaged(repo: git.Repo) -> str:
    return repo.git.diff()

def git_diff_staged(repo: git.Repo) -> str:
    return repo.git.diff("--cached")

def git_commit(repo: git.Repo, message: str) -> str:
    commit = repo.index.commit(message)
    return f"Changes committed successfully with hash {commit.hexsha}"

def git_add(repo: git.Repo, files: list[str]) -> str:
    repo.index.add(files)
    return "Files staged successfully"

def git_reset(repo: git.Repo) -> str:
    repo.index.reset()
    return "All staged changes reset"

def git_log(repo: git.Repo, max_count: int = 10) -> list[str]:
    commits = list(repo.iter_commits(max_count=max_count))
    log = []
    for commit in commits:
        log.append(
            f"Commit: {commit.hexsha}\n"
            f"Author: {commit.author}\n"
            f"Date: {commit.authored_datetime}\n"
            f"Message: {commit.message}\n"
        )
    return log

async def serve(repository: Path | None) -> None:
    logger = logging.getLogger(__name__)

    if repository is not None:
        try:
            git.Repo(repository)
            logger.info(f"Using repository at {repository}")
        except git.InvalidGitRepositoryError:
            logger.error(f"{repository} is not a valid Git repository")
            return

    server = Server("mcp-git")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=GitTools.STATUS,
                description="Shows the working tree status",
                inputSchema=GitStatus.schema(),
            ),
            Tool(
                name=GitTools.DIFF_UNSTAGED,
                description="Shows changes in the working directory that are not yet staged",
                inputSchema=GitDiffUnstaged.schema(),
            ),
            Tool(
                name=GitTools.DIFF_STAGED,
                description="Shows changes that are staged for commit",
                inputSchema=GitDiffStaged.schema(),
            ),
            Tool(
                name=GitTools.COMMIT,
                description="Records changes to the repository",
                inputSchema=GitCommit.schema(),
            ),
            Tool(
                name=GitTools.ADD,
                description="Adds file contents to the staging area",
                inputSchema=GitAdd.schema(),
            ),
            Tool(
                name=GitTools.RESET,
                description="Unstages all staged changes",
                inputSchema=GitReset.schema(),
            ),
            Tool(
                name=GitTools.LOG,
                description="Shows the commit logs",
                inputSchema=GitLog.schema(),
            ),
        ]

    async def list_repos() -> Sequence[str]:
        async def by_roots() -> Sequence[str]:
            if not isinstance(server.request_context.session, ServerSession):
                raise TypeError("server.request_context.session must be a ServerSession")

            if not server.request_context.session.check_client_capability(
                ClientCapabilities(roots=RootsCapability())
            ):
                return []

            roots_result: ListRootsResult = await server.request_context.session.list_roots()
            logger.debug(f"Roots result: {roots_result}")
            repo_paths = []
            for root in roots_result.roots:
                path = root.uri.path
                try:
                    git.Repo(path)
                    repo_paths.append(str(path))
                except git.InvalidGitRepositoryError:
                    pass
            return repo_paths

        def by_commandline() -> Sequence[str]:
            return [str(repository)] if repository is not None else []

        cmd_repos = by_commandline()
        root_repos = await by_roots()
        return [*root_repos, *cmd_repos]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        repo_path = Path(arguments["repo_path"])
        repo = git.Repo(repo_path)

        match name:
            case GitTools.STATUS:
                status = git_status(repo)
                return [TextContent(
                    type="text",
                    text=f"Repository status:\n{status}"
                )]

            case GitTools.DIFF_UNSTAGED:
                diff = git_diff_unstaged(repo)
                return [TextContent(
                    type="text",
                    text=f"Unstaged changes:\n{diff}"
                )]

            case GitTools.DIFF_STAGED:
                diff = git_diff_staged(repo)
                return [TextContent(
                    type="text",
                    text=f"Staged changes:\n{diff}"
                )]

            case GitTools.COMMIT:
                result = git_commit(repo, arguments["message"])
                return [TextContent(
                    type="text",
                    text=result
                )]

            case GitTools.ADD:
                result = git_add(repo, arguments["files"])
                return [TextContent(
                    type="text",
                    text=result
                )]

            case GitTools.RESET:
                result = git_reset(repo)
                return [TextContent(
                    type="text",
                    text=result
                )]

            case GitTools.LOG:
                log = git_log(repo, arguments.get("max_count", 10))
                return [TextContent(
                    type="text",
                    text="Commit history:\n" + "\n".join(log)
                )]

            case _:
                raise ValueError(f"Unknown tool: {name}")

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)
