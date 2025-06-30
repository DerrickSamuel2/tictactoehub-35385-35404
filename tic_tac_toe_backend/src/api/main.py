from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal, Dict, Any
from uuid import uuid4
from datetime import datetime, timedelta
from sqlalchemy import (
    create_engine, Column, String, DateTime, ForeignKey, JSON, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
import os


# Get or use default DB url (for demo, SQLite)
DATABASE_URL = os.environ.get("TICTACTOE_DB_URL", "sqlite:///./tictactoe.db")

Base = declarative_base()
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# FastAPI app with core metadata and tags for OpenAPI
app = FastAPI(
    title="Tic Tac Toe API",
    description=(
        "Backend API for Tic Tac Toe game (start, play, view history, users) "
        "with session management and persistent storage"
    ),
    version="1.0.0",
    openapi_tags=[
        {"name": "Game", "description": "Tic Tac Toe game endpoints"},
        {"name": "Sessions", "description": "User session and management endpoints"},
        {"name": "Health", "description": "Health and system status endpoints"},
        {"name": "Admin", "description": "Administrative endpoints"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=True)
    display_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # No password for demo (JWT/etc for prod)


class Game(Base):
    __tablename__ = "games"
    id = Column(String, primary_key=True, index=True)
    player_x_id = Column(String, ForeignKey("users.id"))
    player_o_id = Column(String, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    state_json = Column(JSON, nullable=False)
    winner = Column(String, nullable=True)
    finished = Column(Boolean, default=False)
    moves_json = Column(JSON, nullable=True)

    player_x = relationship("User", foreign_keys=[player_x_id])
    player_o = relationship("User", foreign_keys=[player_o_id])


class GameSession(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)

    user = relationship("User", foreign_keys=[user_id])


Base.metadata.create_all(bind=engine)


class Move(BaseModel):
    """Request model for sending a move."""
    row: int = Field(
        ..., ge=0, le=2, description="Row index [0-2] on 3x3 board"
    )
    col: int = Field(
        ..., ge=0, le=2, description="Col index [0-2] on 3x3 board"
    )
    player: Literal["X", "O"] = Field(
        ..., description="The moving player, either 'X' or 'O'"
    )


class GameState(BaseModel):
    """Model for representing the current game state."""
    game_id: str = Field(..., description="Unique ID for the game/session")
    board: List[List[Optional[str]]] = Field(
        ..., description="3x3 board state: X, O or null"
    )
    next_player: Optional[Literal["X", "O"]] = Field(
        ..., description="Player whose move is next"
    )
    winner: Optional[Literal["X", "O", "Draw"]] = Field(
        None, description="Who won (if finished)"
    )
    history: List[Dict[str, Any]] = Field(
        default_factory=list, description="History of all moves"
    )


class StartGameResponse(BaseModel):
    """Response model for when a new game is started."""
    game_id: str
    first_player: Literal["X", "O"]
    board: List[List[Optional[str]]]


class UserCreateRequest(BaseModel):
    """Register a new or anonymous user."""
    email: Optional[EmailStr] = None
    display_name: Optional[str] = Field(None, min_length=2, max_length=30)


class UserResponse(BaseModel):
    user_id: str
    email: Optional[EmailStr]
    display_name: Optional[str]
    created_at: datetime


class GameSummary(BaseModel):
    game_id: str
    created_at: datetime
    finished: bool
    winner: Optional[str]
    you_are: Optional[Literal["X", "O"]]
    opponent: Optional[str]


class GameHistoryResponse(BaseModel):
    game_id: str
    moves: List[Dict[str, Any]]
    board: List[List[Optional[str]]]
    winner: Optional[Literal["X", "O", "Draw"]]
    players: Dict[str, Optional[str]]


SESSION_COOKIE_NAME = "ttt_session"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def generate_board():
    """Utility: create and return empty 3x3 Tic Tac Toe board."""
    return [
        [None for _ in range(3)]
        for _ in range(3)
    ]


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
            return line[0]
    if all(all(cell is not None for cell in row) for row in board):
        return "Draw"
    return None


def now_utc():
    return datetime.utcnow()


def get_user_by_session(session_id: Optional[str], db: Session) -> Optional[User]:
    """Fetches a user object from session cookie, or None if not authenticated."""
    if not session_id:
        return None
    session = (
        db.query(GameSession)
        .filter(
            GameSession.id == session_id,
            GameSession.expires_at > now_utc()
        )
        .first()
    )
    if session:
        return session.user
    return None


def create_session_for_user(user: User, db: Session) -> GameSession:
    sid = str(uuid4())
    expires_at = now_utc() + timedelta(days=31)
    sess = GameSession(
        id=sid, user_id=user.id, created_at=now_utc(), expires_at=expires_at
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def user_dict(user: User) -> Dict[str, Any]:
    return {
        "user_id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "created_at": user.created_at,
    }


def ensure_authenticated(request: Request, db: Session) -> User:
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    user = get_user_by_session(sid, db)
    if not user:
        raise HTTPException(
            status_code=401, detail="Authentication required or session expired"
        )
    return user


def check_move_validity(state: GameState, move: Move) -> None:
    """Raise HTTPException if invalid move."""
    if state.winner:
        raise HTTPException(400, "Game is already finished")
    if move.player != state.next_player:
        raise HTTPException(400, "Not this player's turn")
    if state.board[move.row][move.col] is not None:
        raise HTTPException(400, "Cell already occupied")
    if move.player not in ["X", "O"]:
        raise HTTPException(400, "Invalid player symbol")


@app.get("/", tags=["Health"])
def health_check():
    """Check API health status."""
    return {"message": "Healthy"}


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


# PUBLIC_INTERFACE
@app.post("/users/register", response_model=UserResponse, tags=["Sessions"], summary="Register new (or anonymous) user")
def register_user(request: Request, resp: Response, user_req: UserCreateRequest, db: Session = Depends(get_db)):
    """
    Register a new user account or anonymous session.

    Returns:
        UserResponse with session cookie set.
    """
    if user_req.email:
        existing = db.query(User).filter(User.email == user_req.email).first()
        if existing:
            return user_dict(existing)
    user = User(
        id=str(uuid4()),
        email=user_req.email,
        display_name=user_req.display_name,
        created_at=now_utc(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    sess = create_session_for_user(user, db)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=sess.id,
        httponly=True,
        max_age=60 * 60 * 24 * 31,
        path="/"
    )
    return user_dict(user)


# PUBLIC_INTERFACE
@app.post("/users/login_anon", response_model=UserResponse, tags=["Sessions"], summary="Anonymous session for guest")
def anonymous_login(resp: Response, db: Session = Depends(get_db)):
    """
    Start a guest (anonymous) user session.
    """
    user = User(
        id=str(uuid4()),
        email=None,
        display_name=None,
        created_at=now_utc()
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    sess = create_session_for_user(user, db)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=sess.id,
        httponly=True,
        max_age=60 * 60 * 24 * 31,
        path="/"
    )
    return user_dict(user)


# PUBLIC_INTERFACE
@app.get("/users/me", response_model=UserResponse, tags=["Sessions"], summary="Get current user info")
def get_me(request: Request, db: Session = Depends(get_db)):
    """Get the current authenticated user."""
    user = ensure_authenticated(request, db)
    return user_dict(user)


# PUBLIC_INTERFACE
@app.post("/sessions/logout", tags=["Sessions"], summary="Log out (clear session)")
def logout(request: Request, resp: Response, db: Session = Depends(get_db)):
    """Logs out the session, removing session cookie"""
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        resp.delete_cookie(SESSION_COOKIE_NAME)
        return {"message": "Logged out"}
    db.query(GameSession).filter(GameSession.id == sid).delete()
    db.commit()
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return {"message": "Logged out"}


# PUBLIC_INTERFACE
@app.post(
    "/game/start",
    response_model=StartGameResponse,
    tags=["Game"],
    summary="Start a new game",
    description=(
        "Start a new game session and return its game ID, "
        "board, and first player. Session required."
    ),
)
def start_game(request: Request, db: Session = Depends(get_db), opponent_id: Optional[str] = None):
    """
    Start a new Tic Tac Toe game, either solo/casual or vs another user.

    Returns:
        The ID of the new game/session, initial board, and the first player.
    """
    user = ensure_authenticated(request, db)
    player_x = user
    player_o = None
    if opponent_id:
        if opponent_id == user.id:
            raise HTTPException(400, "Cannot play against yourself")
        player_o = db.query(User).filter(User.id == opponent_id).first()
        if not player_o:
            raise HTTPException(404, "Opponent user not found")
    first_player = "X"
    board = generate_board()
    new_state = {
        "game_id": "pending",
        "board": board,
        "next_player": first_player,
        "winner": None,
        "history": [],
    }
    game_obj = Game(
        id=str(uuid4()),
        player_x_id=player_x.id,
        player_o_id=player_o.id if player_o else None,
        created_at=now_utc(),
        updated_at=now_utc(),
        state_json=new_state,
        winner=None,
        finished=False,
        moves_json=[]
    )
    db.add(game_obj)
    db.commit()
    new_state["game_id"] = game_obj.id
    game_obj.state_json = new_state
    db.commit()
    return StartGameResponse(
        game_id=game_obj.id,
        first_player=first_player,
        board=board
    )


# PUBLIC_INTERFACE
@app.get("/game/list", tags=["Game"], response_model=List[GameSummary], summary="List games for user")
def list_games(request: Request, db: Session = Depends(get_db)):
    """
    List all games associated with the current user (as X or O).
    """
    user = ensure_authenticated(request, db)
    games = (
        db.query(Game)
        .filter(
            (Game.player_x_id == user.id)
            |
            (Game.player_o_id == user.id)
        )
        .order_by(
            Game.created_at.desc()
        )
        .all()
    )
    result = []
    for g in games:
        you_are = "X" if g.player_x_id == user.id else (
            "O"
            if g.player_o_id == user.id
            else None
        )
        # These lines now each <= 100 chars
        opponent = None
        if you_are == "X" and g.player_o:
            opponent = g.player_o.display_name or g.player_o.email
        elif you_are == "O" and g.player_x:
            opponent = g.player_x.display_name or g.player_x.email
        result.append(
            GameSummary(
                game_id=g.id,
                created_at=g.created_at,
                finished=g.finished,
                winner=g.winner,
                you_are=you_are,
                opponent=opponent,
            )
        )
    return result


def get_stored_game(game_id: str, db: Session) -> Game:
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


def reconstruct_game_state(game: Game) -> GameState:
    st = game.state_json
    return GameState(
        game_id=game.id,
        board=st["board"],
        next_player=st["next_player"],
        winner=st.get("winner"),
        history=st.get("history") or [],
    )


# PUBLIC_INTERFACE
@app.post(
    "/game/{game_id}/move",
    response_model=GameState,
    tags=["Game"],
    summary="Make a move",
    description=(
        "Submit a move for a given game, validate and update state; "
        "returns new state. You must be a participant."
    ),
)
def make_move(game_id: str, move: Move, request: Request = None, db: Session = Depends(get_db)):
    """
    Make a move in the given Tic Tac Toe game.

    Args:
        game_id: Game session ID.
        move: Move object specifying position and player.

    Returns:
        The updated state of the game after the move.
    """
    user = ensure_authenticated(request, db)
    game = get_stored_game(game_id, db)
    if (
        game.player_x_id != user.id
        and (
            game.player_o_id != user.id
            if game.player_o_id
            else False
        )
    ):
        raise HTTPException(status_code=403, detail="You are not a player in this game")
    state = reconstruct_game_state(game)
    want_player = None
    if state.next_player == "X":
        if game.player_x_id != user.id:
            raise HTTPException(403, "It's not your turn")
        want_player = "X"
    elif state.next_player == "O":
        if not game.player_o_id or game.player_o_id != user.id:
            raise HTTPException(403, "It's not your turn")
        want_player = "O"

    if move.player != want_player:
        raise HTTPException(400, "Move does not match your assigned marker")
    check_move_validity(state, move)
    state.board[move.row][move.col] = move.player
    m_entry = {
        "row": move.row,
        "col": move.col,
        "player": move.player,
        "moved_by": user.display_name or user.email or user.id,
        "timestamp": now_utc().isoformat(),
    }
    state.history.append(m_entry)
    winner = check_winner(state.board)
    state.winner = winner
    if not winner:
        state.next_player = (
            "O"
            if move.player == "X"
            else "X"
        )
    else:
        state.next_player = None

    game.state_json = state.dict()
    game.moves_json = state.history
    game.updated_at = now_utc()
    if winner:
        game.finished = True
        game.winner = winner
    db.commit()
    return state


# PUBLIC_INTERFACE
@app.get(
    "/game/{game_id}/state",
    response_model=GameState,
    tags=["Game"],
    summary="Get game state",
    description="Retrieve the current state of a game session by ID.",
)
def get_game_state(game_id: str, request: Request = None, db: Session = Depends(get_db)):
    """
    Get the current state of a specific game by its ID.

    Args:
        game_id: Game session ID.

    Returns:
        GameState object describing the board, turn, etc.
    """
    user = ensure_authenticated(request, db)
    game = get_stored_game(game_id, db)
    if user.id not in [game.player_x_id, game.player_o_id]:
        raise HTTPException(403, "You do not have access to this game")
    return reconstruct_game_state(game)


# PUBLIC_INTERFACE
@app.get(
    "/game/{game_id}/history",
    response_model=GameHistoryResponse,
    tags=["Game"],
    summary="Get move history",
    description="Get list of all moves made in this game.",
)
def get_game_history(game_id: str, request: Request = None, db: Session = Depends(get_db)):
    """
    Retrieve the history of moves for a specific game.

    Args:
        game_id: Game session ID.

    Returns:
        List of moves with move details and players.
    """
    user = ensure_authenticated(request, db)
    game = get_stored_game(game_id, db)
    if user.id not in [game.player_x_id, game.player_o_id]:
        raise HTTPException(403, "You do not have access to this game")
    players = {}
    if game.player_x:
        players["X"] = game.player_x.display_name or game.player_x.email
    if game.player_o:
        players["O"] = game.player_o.display_name or game.player_o.email
    state = reconstruct_game_state(game)
    return GameHistoryResponse(
        game_id=game.id,
        moves=state.history,
        board=state.board,
        winner=state.winner,
        players=players
    )


@app.get(
    "/game/history/all",
    response_model=List[GameHistoryResponse],
    tags=["Game"],
    summary="Get all games' histories for user",
    description="Retrieve the history of all games for the current user."
)
def get_all_game_histories(request: Request, db: Session = Depends(get_db)):
    """
    Get a list of game history objects for all games belonging to current user.
    """
    user = ensure_authenticated(request, db)
    games = (
        db.query(Game)
        .filter(
            (Game.player_x_id == user.id)
            |
            (Game.player_o_id == user.id)
        )
        .order_by(
            Game.created_at.desc()
        )
        .all()
    )
    result = []
    for game in games:
        state = reconstruct_game_state(game)
        players = {}
        if game.player_x:
            players["X"] = game.player_x.display_name or game.player_x.email
        if game.player_o:
            players["O"] = game.player_o.display_name or game.player_o.email
        result.append(
            GameHistoryResponse(
                game_id=game.id,
                moves=state.history,
                board=state.board,
                winner=state.winner,
                players=players
            )
        )
    return result


@app.get("/admin/users", tags=["Admin"], summary="List all users (admin/debug)")
def admin_users(db: Session = Depends(get_db)):
    all_users = db.query(User).all()
    return [
        {"id": u.id, "email": u.email, "display_name": u.display_name}
        for u in all_users
    ]


@app.get("/admin/games", tags=["Admin"], summary="List all games (admin/debug)")
def admin_games(db: Session = Depends(get_db)):
    all_games = db.query(Game).all()
    return [
        {
            "id": g.id,
            "created_at": g.created_at,
            "player_x": g.player_x_id,
            "player_o": g.player_o_id,
            "winner": g.winner,
            "finished": g.finished,
        }
        for g in all_games
    ]
