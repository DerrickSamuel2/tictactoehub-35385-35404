from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
from uuid import uuid4


# FastAPI app with core metadata and tags for OpenAPI
app = FastAPI(
    title="Tic Tac Toe API",
    description=(
        "Backend API for Tic Tac Toe game (start, play, view history) "
        "with session management"
    ),
    version="1.0.0",
    openapi_tags=[
        {"name": "Game", "description": "Tic Tac Toe game endpoints"},
        {"name": "Sessions", "description": "User session and management endpoints"},
        {"name": "Health", "description": "Health and system status endpoints"},
    ],
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Health"])
def health_check():
    """Check API health status."""
    return {"message": "Healthy"}


# ========================
# Models and Data Handling
# ========================

class Move(BaseModel):
    """Request model for sending a move."""
    row: int = Field(
        ...,
        ge=0,
        le=2,
        description="Row index [0-2] on 3x3 board"
    )
    col: int = Field(
        ...,
        ge=0,
        le=2,
        description="Col index [0-2] on 3x3 board"
    )
    player: Literal["X", "O"] = Field(
        ...,
        description="The moving player, either 'X' or 'O'"
    )


class GameState(BaseModel):
    """Model for representing the current game state."""
    game_id: str = Field(
        ...,
        description="Unique ID for the game/session"
    )
    board: List[List[Optional[str]]] = Field(
        ...,
        description="3x3 board state: X, O or null"
    )
    next_player: Literal["X", "O"] = Field(
        ...,
        description="Player whose move is next"
    )
    winner: Optional[Literal["X", "O", "Draw"]] = Field(
        None,
        description="Who won (if finished)"
    )
    history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="History of all moves"
    )


class StartGameResponse(BaseModel):
    """Response model for when a new game is started."""
    game_id: str
    first_player: Literal["X", "O"]
    board: List[List[Optional[str]]]


# In-memory storage for simplicity (to be moved to DB later)
games: Dict[str, GameState] = {}


# ================================
# Session and Utility Functions
# ================================

def generate_board():
    """Utility: create and return empty 3x3 Tic Tac Toe board."""
    return [
        [None for _ in range(3)]
        for _ in range(3)
    ]


def get_game(game_id: str) -> GameState:
    """Fetches the game state or raises 404."""
    game = games.get(game_id)
    if not game:
        raise HTTPException(
            status_code=404,
            detail="Game not found"
        )
    return game


def check_winner(board: List[List[Optional[str]]]) -> Optional[str]:
    """Determine if game is won, drawn, or ongoing."""
    lines = (
        # Rows
        board[0], board[1], board[2],
        # Columns
        [board[i][0] for i in range(3)],
        [board[i][1] for i in range(3)],
        [board[i][2] for i in range(3)],
        # Diagonals
        [board[i][i] for i in range(3)],
        [board[i][2 - i] for i in range(3)],
    )
    for line in lines:
        if line[0] and line.count(line[0]) == 3:
            return line[0]  # X or O

    if all(all(cell is not None for cell in row) for row in board):
        return "Draw"
    return None


# ================================
# API Endpoints
# ================================

# PUBLIC_INTERFACE
@app.post(
    "/game/start",
    response_model=StartGameResponse,
    tags=["Game"],
    summary="Start a new game",
    description=(
        "Start a new game session and return its game ID, "
        "board, and first player."
    ),
)
def start_game():
    """
    Start a new Tic Tac Toe game.

    Returns:
        The ID of the new game/session, initial board, and the first player.
    """
    game_id = str(uuid4())
    board = generate_board()
    first_player = "X"
    new_game = GameState(
        game_id=game_id,
        board=board,
        next_player=first_player,
        winner=None,
        history=[]
    )
    games[game_id] = new_game
    return StartGameResponse(
        game_id=game_id,
        first_player=first_player,
        board=board
    )


# PUBLIC_INTERFACE
@app.post(
    "/game/{game_id}/move",
    response_model=GameState,
    tags=["Game"],
    summary="Make a move",
    description=(
        "Submit a move for a given game, validate and update state; "
        "returns new state."
    ),
)
def make_move(game_id: str, move: Move):
    """
    Make a move in the given Tic Tac Toe game.

    Args:
        game_id: Game session ID.
        move: Move object specifying position and player.

    Returns:
        The updated state of the game after the move.
    """
    game = get_game(game_id)
    if game.winner:
        raise HTTPException(
            status_code=400,
            detail="Game is already finished"
        )
    if move.player != game.next_player:
        raise HTTPException(
            status_code=400,
            detail="Not this player's turn"
        )
    if game.board[move.row][move.col] is not None:
        raise HTTPException(
            status_code=400,
            detail="Cell already occupied"
        )
    if move.player not in ["X", "O"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid player symbol"
        )

    # Execute move
    game.board[move.row][move.col] = move.player
    game.history.append({
        "row": move.row,
        "col": move.col,
        "player": move.player
    })

    # Check for winner or draw
    winner = check_winner(game.board)
    game.winner = winner
    game.next_player = "O" if move.player == "X" else "X"

    return game


# PUBLIC_INTERFACE
@app.get(
    "/game/{game_id}/state",
    response_model=GameState,
    tags=["Game"],
    summary="Get game state",
    description="Retrieve the current state of a game session by ID.",
)
def get_game_state(game_id: str):
    """
    Get the current state of a specific game by its ID.

    Args:
        game_id: Game session ID.

    Returns:
        GameState object describing the board, turn, etc.
    """
    return get_game(game_id)


# PUBLIC_INTERFACE
@app.get(
    "/game/{game_id}/history",
    tags=["Game"],
    summary="Get move history",
    description="Get list of all moves made in this game.",
)
def get_game_history(game_id: str):
    """
    Retrieve the history of moves for a specific game.

    Args:
        game_id: Game session ID.

    Returns:
        List of moves with move details.
    """
    game = get_game(game_id)
    return {
        "game_id": game_id,
        "history": game.history
    }


# PUBLIC_INTERFACE
@app.get(
    "/openapi_help",
    tags=["Health"],
    summary="Documentation for real-time/WebSocket integration",
    description="Usage documentation for real-time endpoints if available.",
)
def websocket_usage_note():
    """
    This version of the Tic Tac Toe backend currently supports REST APIs for move
    processing and game management. Future versions may support WebSocket endpoints for
    real-time updates (not included yet).
    """
    return {
        "note": (
            "Use REST APIs to create game, make moves, and get results. "
            "WebSocket real-time features will be added in a future update."
        )
    }
