# yt_trim_gui_lobster.py  – YouTube downloader + loss-less clip-trimmer
# pip install yt-dlp PyQt6 ffmpeg-python
# Make sure ffmpeg is on PATH.  (Optional) place Lobster-Regular.ttf beside this file.

from __future__ import annotations
import sys, threading, subprocess, tempfile, shutil
from pathlib import Path
from datetime import timedelta

from yt_dlp import YoutubeDL
from PyQt6.QtCore import (QSize, Qt, QUrl, QObject, pyqtSignal, QPropertyAnimation,
                         QEasingCurve, QTimer, pyqtProperty, QAbstractAnimation,
                         QPoint, QRect)
                        
from PyQt6.QtGui  import QFontDatabase, QFont, QTransform, QPixmap, QCursor, QPainter,QColor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QLabel, QSlider, QMessageBox, QFileDialog, QStyle, QProgressBar,
    QStackedLayout, QGraphicsOpacityEffect, QSizePolicy, QGraphicsDropShadowEffect
)

from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget


# ── custom cursor ─────────────────────────────────────────────
from PyQt6.QtGui import QPixmap, QCursor

class GlowButton(QPushButton):
    def __init__(self, txt: str, glow_color: QColor, parent=None):
        super().__init__(txt, parent)

        self._fx = QGraphicsDropShadowEffect(self)
        self._fx.setBlurRadius(0)          # start “off”
        self._fx.setColor(glow_color)
        self._fx.setOffset(0, 0)
        self.setGraphicsEffect(self._fx)

        self._anim = QPropertyAnimation(self._fx, b"blurRadius", self)
        self._anim.setDuration(180)        # ms

    # strength = 0 (invisible) … 1 (full)
    @pyqtProperty(float)
    def _strength(self):
        return self._glow_fx.strength() if hasattr(self._glow_fx, "strength") else 0

    @_strength.setter
    def _strength(self, value):
        # Qt6 doesn’t expose ‘strength’, so we fake it by switching enabled on/off
        self._glow_fx.setEnabled(value > 0.01)

    # hover in → fade glow on
    def enterEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._fx.blurRadius())
        self._anim.setEndValue(18)         # full glow
        self._anim.start()
        super().enterEvent(e)
    # hover out → fade glow off
    def leaveEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._fx.blurRadius())
        self._anim.setEndValue(0)          # fade out
        self._anim.start()
        super().leaveEvent(e)



def apply_custom_cursor(app, hot_x: int = 0, hot_y: int = 0,
                        box: int = 64):
    """
    Centres cursor.png inside a transparent *box*×*box* pixmap,
    so the OS hit-box is exactly *box*² even if the icon itself is smaller.
    """
    png = Path(__file__).with_name("cursor.png")
    pix_orig = QPixmap(str(png))
    if pix_orig.isNull():
        print("⚠️ cursor.png missing/invalid.")
        return

    # make a transparent canvas
    canvas = QPixmap(box, box)
    canvas.fill(Qt.GlobalColor.transparent)

    # paint the original into the centre
    painter = QPainter(canvas)
    x = (box - pix_orig.width())  // 2
    y = (box - pix_orig.height()) // 2
    painter.drawPixmap(x, y, pix_orig)
    painter.end()

    # default hot-spot = centre of canvas unless caller overrides
    if hot_x == hot_y == 0:
        hot_x, hot_y = box // 2, box // 2

    app.setOverrideCursor(QCursor(canvas, hot_x, hot_y))

# ────────────────────── theme helpers ─────────────────────────
def apply_lobster_theme(app: QApplication):
    """Load local Lobster font (if present) and apply navy / coral palette."""
    # 1) try font
    ttf = Path(__file__).with_name("Lobster-Regular.ttf")
    if ttf.exists():
        QFontDatabase.addApplicationFont(str(ttf))
        app.setFont(QFont("Lobster"))      # family name inside the TTF

    # 2) palette colours
    NAVY   = "#0f1f2d"
    RED    = "#b22222"
    CORAL  = "#ff6347"
    CREAM  = "#fff5ee"
    TRACK  = "#324355"

    app.setStyleSheet(f"""
                      
        QWidget      {{ background:{NAVY};  color:{CREAM};
                          font-family:"Lobster","Segoe UI",sans-serif; }}
        QLineEdit       {{ background:#132435; border:2px solid {CORAL};
                          border-radius:6px; padding:6px 10px; }}
        QPushButton     {{ background:{RED}; border:none; padding:8px 20px;
                          border-radius:8px; }}
        QPushButton:hover   {{ background:{CORAL}; }}
        QPushButton:pressed {{ background:{CORAL}AA; }}
        QProgressBar        {{ background:#132435; border:1px solid {TRACK};
                              border-radius:4px; height:10px; }}
        QProgressBar::chunk {{ background:{CORAL}; border-radius:4px; }}
        QSlider::groove:horizontal {{ height:6px; background:{TRACK}; border-radius:3px; }}
        QSlider::handle:horizontal {{ width:14px; background:{CORAL};
                                      margin:-5px 0; border-radius:7px; }}
        QSlider::sub-page:horizontal {{ background:{RED}; border-radius:3px; }}
        QLabel#timeLabel  {{ font-size:14px; }}
        #titleBar         {{ background:{NAVY}; }}
        #titleLabel       {{ color:{CREAM}; font-size:16px; }}

        /*  MINIMISE  ───────────────────────────────────────── */
        #minBtn {{                  /* ← default (idle) state  */
            color: #fff5ee;                /* always visible (cream)  */
            background: transparent;
            border: none; border-radius: 4px;
            
        }}
        #minBtn:hover    {{ background: #324355;   }}
        #minBtn:pressed  {{ background: #b22222AA; }}

        /*  CLOSE  ✕  ───────────────────────────────────────── */
        #closeBtn {{
            color: #b22222;     
            background: transparent;
            border: none; border-radius: 4px;
            font-size: 16px;
        }}
        #closeBtn:hover   {{ background: #b22222;    color: #fff5ee;  }}
        #closeBtn:pressed {{ background: #b22222AA;  color: #fff5ee;}}

        #mainWindow {{ border-radius:14px; overflow:hidden; }}
        QLabel#loadingLbl {{          /*   ← new   */
            font-size: 32px;          /* pick any px or pt size */
            }}
    """)


# ────────────────────── utility fns ───────────────────────────
INVALID = r'<>:"/\\|?*'
def sanitize(t: str, repl: str="-") -> str:
    return "".join(c if c not in INVALID else repl for c in t)

def hhmmss(sec: int | float) -> str:
    return str(timedelta(seconds=int(sec)))


# ────────────────────── downloader worker ─────────────────────
class DownloadWorker(QObject):
    finished = pyqtSignal(Path, int)   # path, duration
    error    = pyqtSignal(str)
    progress = pyqtSignal(int)

    def __init__(self, url: str, tmp: Path):
        super().__init__()
        self.url, self.tmp = url, tmp

    def run(self):
        try:
            with YoutubeDL({}) as probe:
                info = probe.extract_info(self.url, download=False)
                dur  = info.get("duration") or 0
                title = info.get("title") or "clip"

            safe = sanitize(title)
            out  = str(self.tmp / f"{safe}.%(ext)s")

            def phook(d):
                if d["status"] == "downloading":
                    t = d.get("total_bytes") or d.get("total_bytes_estimate")
                    if t: self.progress.emit(int(d.get("downloaded_bytes",0)*100/t))

            ydl_opts = {
                "format": "bv*[vcodec*=avc1][ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                "concurrent_fragment_downloads": 8,
                "outtmpl": out,
                "merge_output_format": "mp4",
                "quiet": True,
                "progress_hooks": [phook],
            }
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])

            mp4 = (self.tmp / f"{safe}.mp4").resolve()
            if not mp4.exists():
                raise RuntimeError("Download finished but .mp4 not found.")
            self.progress.emit(100)
            self.finished.emit(mp4, dur)

        except Exception as e:
            self.error.emit(str(e))

# ────────────────────── splash screen ─────────────────────────
class SplashScreen(QWidget):
    """Fades a rotating lobster PNG in/out, then calls `done_cb()`."""
    def __init__(self, png_path: Path, done_cb: callable):
        super().__init__()
        self.setStyleSheet("background:#0f1f2d;")          # NAVY
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(900, 540)

        # ---------- central pixmap ----------
        self._pix = QPixmap(str(png_path)).scaled(
            350, 350, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self._label = QLabel(self)
        self._label.setPixmap(self._pix)
        self._label.setFixedSize(self._pix.size())
        self._label.move(
            (self.width()  - self._pix.width())  // 2,
            (self.height() - self._pix.height()) // 2,
        )

        # ---------- opacity  ----------
        self._fx = QGraphicsOpacityEffect(self._label)
        self._label.setGraphicsEffect(self._fx)

        self._fade_in  = QPropertyAnimation(self._fx, b"opacity")
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setDuration(1000)

        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(1000)

        self._fade_out = QPropertyAnimation(self._fx, b"opacity")
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setDuration(1000)

        # ---------- rocking animation ----------
        self._angle = 0.0  # backing field for property

        self._rock = QPropertyAnimation(self, b"angle")
        self._rock.setStartValue(-6)
        self._rock.setEndValue(6)
        self._rock.setDuration(1200)
        self._rock.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._rock.setDirection(QAbstractAnimation.Direction.Forward)

        # ping-pong (flip direction each time it finishes)
        self._rock.finished.connect(self._alternate_direction)

        # ---------- sequence wiring ----------
        self._fade_in.finished.connect(self._hold.start)
        self._hold.timeout.connect(self._start_crossfade) 
        self._fade_out.finished.connect(self._finish)

        self._done_cb = done_cb  # save callback so _finish can use it
    def _start_crossfade(self):
            self._done_cb()           # show + fade-in main
            self._fade_out.start()    # now fade *this* splash away
    # synthetic property so QPropertyAnimation can rotate us
    @pyqtProperty(float)
    def angle(self) -> float:
        return self._angle

    @angle.setter
    def angle(self, val: float):
        self._angle = val
        tr = QTransform().rotate(val)
        self._label.setPixmap(
            self._pix.transformed(tr, Qt.TransformationMode.SmoothTransformation)
        )

    # start animations when the widget becomes visible
    def showEvent(self, ev):
        self._fade_in.start()
        self._rock.start()           # first leg (-6 → +6)
        super().showEvent(ev)

    # alternate rocker direction smoothly
    def _alternate_direction(self):
        new_dir = (QAbstractAnimation.Direction.Backward
                   if self._rock.direction() == QAbstractAnimation.Direction.Forward
                   else QAbstractAnimation.Direction.Forward)
        self._rock.setDirection(new_dir)
        self._rock.start()

    def _finish(self):
        self._rock.stop()
        self.close()

class FloatyLabel(QLabel):
    """QLabel that paints its pixmap shifted vertically by self.dy."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._dy = 0.0               # logical pixels

    @pyqtProperty(float)
    def dy(self):
        return self._dy

    @dy.setter
    def dy(self, v: float):
        self._dy = v
        self.update()                # schedule repaint

    # paint pixmap with vertical offset
    def paintEvent(self, ev):
        if self.pixmap() is None:
            return super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        p.translate(0, self._dy)
        p.drawPixmap(0, 0, self.pixmap())


# ────────────────────── RangeSlider ──────────────────────────
class RangeSlider(QWidget):
    """Horizontal slider with three handles: start, position and end."""

    lowerValueChanged = pyqtSignal(int)
    upperValueChanged = pyqtSignal(int)
    valueChanged = pyqtSignal(int)

    def __init__(self, minimum=0, maximum=99, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self._min = minimum
        self._max = maximum
        self._lower = minimum
        self._upper = maximum
        self._value = minimum
        self._bar_h = 6
        self._handle_r = 8
        self._active = None
        self.setMouseTracking(True)

    # ----- value helpers -----
    def lowerValue(self) -> int: return self._lower
    def upperValue(self) -> int: return self._upper
    def setRange(self, a: int, b: int):
        self._min, self._max = a, b
        self._lower, self._upper = a, b
        self._value = max(a, min(self._value, b))
        self.update()

    def setLowerValue(self, v: int):
        v = max(self._min, min(v, self._upper))
        if v != self._lower:
            self._lower = v
            if self._value < v:
                self._value = v
                self.valueChanged.emit(v)
            self.lowerValueChanged.emit(v)
            self.update()

    def setUpperValue(self, v: int):
        v = min(self._max, max(v, self._lower))
        if v != self._upper:
            self._upper = v
            if self._value > v:
                self._value = v
                self.valueChanged.emit(v)
            self.upperValueChanged.emit(v)
            self.update()

    def value(self) -> int:
        return self._value

    def setValue(self, v: int):
        v = max(self._lower, min(v, self._upper))
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)
            self.update()

    def _val_to_pos(self, v: int) -> int:
        span = self._max - self._min or 1
        w = self.width() - 2 * self._handle_r
        return int((v - self._min) / span * w) + self._handle_r

    def _pos_to_val(self, x: float) -> int:
        span = self._max - self._min or 1
        w = self.width() - 2 * self._handle_r
        x = max(self._handle_r, min(x, self.width() - self._handle_r)) - self._handle_r
        return int(round(x / w * span + self._min))

    # ----- painting -----
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() // 2
        left = self._val_to_pos(self._lower)
        right = self._val_to_pos(self._upper)
        mid = self._val_to_pos(self._value)

        # groove
        track = QRect(self._handle_r, cy - self._bar_h // 2,
                      self.width() - 2 * self._handle_r, self._bar_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#324355"))
        p.drawRoundedRect(track, 3, 3)

        # selection
        sel = QRect(left, cy - self._bar_h // 2, right - left, self._bar_h)
        p.setBrush(QColor("#b22222"))
        p.drawRoundedRect(sel, 3, 3)

        # handles
        p.setBrush(QColor("#ff6347"))
        for x in (left, right):
            p.drawEllipse(QPoint(x, cy), self._handle_r, self._handle_r)
        p.setBrush(QColor("#ffe070"))
        p.drawEllipse(QPoint(mid, cy), self._handle_r, self._handle_r)

    # ----- mouse -----
    def mousePressEvent(self, e):
        lx = self._val_to_pos(self._lower)
        mx = self._val_to_pos(self._value)
        ux = self._val_to_pos(self._upper)
        distances = {
            'low': abs(e.position().x() - lx),
            'mid': abs(e.position().x() - mx),
            'high': abs(e.position().x() - ux),
        }
        self._active = min(distances, key=distances.get)
        self._move(e)

    def mouseMoveEvent(self, e):
        if self._active:
            self._move(e)

    def mouseReleaseEvent(self, e):
        self._active = None

    def _move(self, e):
        v = self._pos_to_val(e.position().x())
        if self._active == 'low':
            self.setLowerValue(v)
            if self._value < self._lower:
                self.setValue(self._lower)
        elif self._active == 'high':
            self.setUpperValue(v)
            if self._value > self._upper:
                self.setValue(self._upper)
        elif self._active == 'mid':
            self.setValue(v)

# ────────────────────── Main GUI class ────────────────────────
class MainUI(QWidget):
    def __init__(self):
        self.filled_once: set[int] = set()
        super().__init__()
        self._drag_pos = QPoint()

        # ---------- window flags / size / css object ----------
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setFixedSize(900, 540)
        self.setObjectName("mainWindow")

        # ---------- custom title-bar ----------
        title_bar = QWidget(objectName="titleBar")
        trow = QHBoxLayout(title_bar)
        trow.setContentsMargins(10, 2, 4, 2)

        title_lbl = QLabel("Lobster Clipper", objectName="titleLabel")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter |
                        Qt.AlignmentFlag.AlignLeft)

        btn_min   = GlowButton("–", QColor("#ff6347"))   # coral glow
        btn_close = GlowButton("✕", QColor("#b22222"))   # brick-red glow
        btn_min.setObjectName("minBtn")
        btn_close.setObjectName("closeBtn")
        btn_min.setFixedSize(24, 24)
        btn_close.setFixedSize(24, 24)



        # make the title-bar draggable
        title_bar.mousePressEvent = self._start_move
        title_bar.mouseMoveEvent  = self._moving
        btn_close.clicked.connect(self.close)
        btn_min.clicked.connect(self.showMinimized)
        # load icons once (they live next to the .py file)

# ---- DOWNLOADER pane ----------------------------------------------------
        self.url_edit = QLineEdit(placeholderText="Paste YouTube URL…")        
        self.dl_btn   = QPushButton("Load video")
        

        
# ①  Poster (ocean-floor art)
        self.poster = QLabel(objectName="poster")
        self.poster.setPixmap(QPixmap("ocean_poster.png"))
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster.setScaledContents(True)
        self.poster.setMinimumHeight(180)              # optional
        base_dir      = Path(__file__).parent
        self.pix_empty  = QPixmap(str(base_dir / "grayed_drumstick.png"))
        self.pix_filled = QPixmap(str(base_dir / "drumstick.png"))
        TOTAL_ICONS = 10
        self.drum_row   = QHBoxLayout()
        self.drum_icons = []
        self.float_anims = []     
        for idx in range(TOTAL_ICONS):
            lbl = FloatyLabel()
            lbl.setPixmap(self.pix_empty)          # starts greyed-out
            lbl.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
            self.drum_row.addWidget(lbl)
            self.drum_icons.append(lbl)
    # gentle bobbing animation
            anim = QPropertyAnimation(lbl, b"dy", self)
            anim.setDuration(2400)
            anim.setLoopCount(-1)
            anim.setEasingCurve(QEasingCurve.Type.Linear)
            anim.setKeyValueAt(0.0, -2)
            anim.setKeyValueAt(0.5,  2)
            anim.setKeyValueAt(1.0, -2)
            anim.setCurrentTime(int(idx / TOTAL_ICONS * anim.duration()))
            anim.start()
            self.float_anims.append(anim)


        self.drum_widget = QWidget()
        self.drum_widget.setLayout(self.drum_row)
        self.drum_widget.hide()         
        
# ②  Loading text (hidden at first)
        self.loading_lbl = QLabel("Loading…", objectName="loadingLbl")
        self.loading_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.loading_lbl.hide()    
        self.bar = QProgressBar()               # start invisible
        self.bar.setFixedWidth(320)
        self.bar.hide()    

        center_box = QVBoxLayout()   
        center_box.addWidget(self.loading_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        center_box.addWidget(self.drum_widget, alignment=Qt.AlignmentFlag.AlignHCenter) # sticks under the text

 # 0️⃣  ────── Loading-dots timer ──────
        self._dot_timer   = QTimer(self)
        self._dot_timer.setInterval(400)          # 0.4 s per frame
        self._dot_phase   = 0                     # running counter
        self._dot_timer.timeout.connect(self._tick_loading_dots)

#  sprite-based loader  (10 icons, adjust if you like)

# --- main column for the downloader screen -------------------
        dcol = QVBoxLayout()
        dcol.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(self.url_edit)
        top.addWidget(self.dl_btn)
        dcol.addLayout(top)

        dcol.addWidget(self.poster)     # <-- poster visible by default
        dcol.addStretch(1)          # ↑ push everything that follows toward centre
        dcol.addLayout(center_box)  # ← our little stack (label + bar)
        dcol.addStretch(1)          # ↓ balances the first stretch

        down_screen = QWidget(); 
        down_screen.setLayout(dcol)

        # ---------- TRIM-STUDIO pane ----------
        self.video_widget = QVideoWidget()
        self.player = QMediaPlayer(); self.audio = QAudioOutput()
        self.audio.setVolume(1.0)
        self.player.setVideoOutput(self.video_widget); self.player.setAudioOutput(self.audio)

        self.play_btn = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "")
        self.time_lbl = QLabel("00:00 / 00:00", objectName="timeLabel")

        self.range_slider = RangeSlider()
        self.range_slider.setValue(0)
        self.start_lbl, self.end_lbl = QLabel("00:00:00"), QLabel("00:00:00")
        self.trim_btn = QPushButton("Trim & Save")

        studio = QVBoxLayout(); studio.addWidget(self.video_widget, 1)
        row = QHBoxLayout();
        row.addWidget(self.range_slider, 1)
        row.addWidget(self.start_lbl)
        row.addWidget(self.end_lbl)
        studio.addLayout(row)
        ctl = QHBoxLayout(); ctl.addWidget(self.play_btn); ctl.addWidget(self.time_lbl)
        ctl.addStretch();     ctl.addWidget(self.trim_btn); studio.addLayout(ctl)
        edit_screen = QWidget(); edit_screen.setLayout(studio)

        # ---------- STACK (create BEFORE we add it to root!) ----------
        self.stack = QStackedLayout()
        self.stack.addWidget(down_screen)
        self.stack.addWidget(edit_screen)

        # ---------- root layout ----------
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(title_bar)
        root.addLayout(self.stack)

        # ---------- state ----------
        self.video_path: Path | None = None
        self.duration = 0
        self.tmp_dir: Path | None = None

        # ---------- signals ----------
        self.dl_btn.clicked.connect(self.start_download)
        self.play_btn.clicked.connect(self.toggle_play)
        self.player.positionChanged.connect(self.update_time)
        self.player.durationChanged.connect(self.got_duration)

        self.range_slider.valueChanged.connect(lambda v: self.player.setPosition(int(v*1000)))
        self.range_slider.lowerValueChanged.connect(self._start_changed)
        self.range_slider.upperValueChanged.connect(self._end_changed)
        self.range_slider.lowerValueChanged.connect(lambda v: self.preview(v))
        self.range_slider.upperValueChanged.connect(lambda v: self.preview(v))

        self.trim_btn.clicked.connect(self.trim_save)
    def bounce_once(self, lbl: FloatyLabel):
        """Play a quick bounce on *lbl* without interrupting its hover."""
        bounce = QPropertyAnimation(lbl, b"dy", self)
        bounce.setStartValue(-12)                  # jump up 12 px
        bounce.setEndValue(0)                      # settle back
        bounce.setDuration(350)
        bounce.setEasingCurve(QEasingCurve.Type.OutBounce)
        bounce.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

# ── Title-bar drag helpers ───────────────────────────────────
    def _start_move(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            # distance from window-origin to click-point
            self._drag_pos = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            e.accept()

    def _moving(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()
    def fade_out_widget(self, w: QWidget):
      
       eff = QGraphicsOpacityEffect(w)
       w.setGraphicsEffect(eff)
       anim = QPropertyAnimation(eff, b'opacity', self)
       anim.setDuration(400)
       anim.setStartValue(1)
       anim.setEndValue(0)
       anim.finished.connect(w.hide)
       anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
       
    def _tick_loading_dots(self):
        self._dot_phase = (self._dot_phase + 1) % 4         # 0→1→2→3→0…
        self.loading_lbl.setText("Loading" + "."*self._dot_phase)

    # ── Downloader logic ──
    def start_download(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self,"No URL","Paste a YouTube URL."); return
        # hide the URL controls as soon as we start
        self.url_edit.setVisible(False)
        self.dl_btn.setVisible(False)
        self.drum_widget.setVisible(True)

        self.loading_lbl.setText("Loading")   # reset clean text
        self._dot_phase = 0                   # reset counter
        self._dot_timer.start()               # <-- start animation


# --- show the emoji loader, hide the bar ---
        self.bar.setVisible(False)
        # (button already invisible, but keep it disabled in case you ever show it)
        self.dl_btn.setEnabled(False)

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="lobster_"))
        worker = DownloadWorker(url, self.tmp_dir)
        self.worker = worker
        worker.progress.connect(self.update_drumsticks)
        worker.finished.connect(self.dl_done)
        worker.error.connect(self.dl_error)
        threading.Thread(target=worker.run, daemon=True).start()     

        self.poster.hide()            # swap widgets
        self.loading_lbl.show()   
        self.drum_widget.show()       # show the sticks
    def update_drumsticks(self, pct: int):
        filled = round(pct / 100 * len(self.drum_icons))

        for i, lbl in enumerate(self.drum_icons):
            target = self.pix_filled if i < filled else self.pix_empty

            # ① swap pixmap only if it’s different
            if lbl.pixmap().cacheKey() != target.cacheKey():
                lbl.setPixmap(target)

            # ② bounce exactly once, the first time it turns orange
            if i < filled and i not in self.filled_once:
                self.bounce_once(lbl)
                self.filled_once.add(i)
    def dl_done(self, path:Path, dur:int):
        self.video_path, self.duration = path, dur
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.range_slider.setRange(0, dur)
        self.range_slider.setLowerValue(0)
        self.range_slider.setUpperValue(dur)
        self.range_slider.setValue(0)
        self.stack.setCurrentIndex(1); self.bar.setVisible(False); self.dl_btn.setEnabled(True)
        self.stack.setCurrentIndex(1)
        self.bar.setVisible(False)
        self.drum_widget.setVisible(False)

        self.loading_lbl.hide()
        self.drum_widget.hide()
        self.poster.show()

        self._dot_timer.stop()
        
    def dl_error(self, msg):
        QMessageBox.critical(self,"Download error",msg)
        self.bar.setVisible(False)
        self.drum_widget.setVisible(False)
        # show the URL controls again so the user can retry
        self.url_edit.setVisible(True)
        self.dl_btn.setVisible(True)
        self.dl_btn.setEnabled(True)
        self.loading_lbl.hide()
        self.poster.show()

        self._dot_timer.stop()

    # ── Studio helpers ──
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            icon_enum = QStyle.StandardPixmap.SP_MediaPlay
        else:
            self.player.play()
            icon_enum = QStyle.StandardPixmap.SP_MediaPause
        self.play_btn.setIcon(self.style().standardIcon(icon_enum))

    def update_time(self, pos_ms):
        tot = self.duration or self.player.duration() // 1000
        sec = pos_ms / 1000
        self.time_lbl.setText(f"{hhmmss(sec)} / {hhmmss(tot)}")

        low = self.range_slider.lowerValue()
        high = self.range_slider.upperValue()
        if sec < low:
            sec = low
            self.player.setPosition(int(sec * 1000))
        elif sec > high:
            sec = high
            self.player.setPosition(int(sec * 1000))
            self.player.pause()
            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

        self.range_slider.blockSignals(True)
        self.range_slider.setValue(int(sec))
        self.range_slider.blockSignals(False)

    def got_duration(self, ms):
        if not self.duration:
            self.duration = ms // 1000
            self.range_slider.setRange(0, self.duration)
            self.range_slider.setUpperValue(self.duration)

    def preview(self, s):
        self.player.pause(); self.player.setPosition(int(s*1000)); self.update_time(s*1000)

    def _start_changed(self, v: int):
        self.start_lbl.setText(hhmmss(v))
        if self.range_slider.value() < v:
            self.range_slider.setValue(v)

    def _end_changed(self, v: int):
        self.end_lbl.setText(hhmmss(v))
        if self.range_slider.value() > v:
            self.range_slider.setValue(v)

    # fine-tune keys
    def keyPressEvent(self, e):
        step = 0.1 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        k = e.key()
        if k in (Qt.Key.Key_A, Qt.Key.Key_Left):
            self.nudge(-step)
        elif k in (Qt.Key.Key_D, Qt.Key.Key_Right):
            self.nudge(step)
        elif k == Qt.Key.Key_Space:
            self.toggle_play()
        else:
            super().keyPressEvent(e)
    def nudge(self, dt):
        low = self.range_slider.lowerValue()
        high = self.range_slider.upperValue()
        p = max(low, min(high, self.player.position() / 1000 + dt))
        self.player.setPosition(int(p * 1000))

    # ── Trim ──
    def trim_save(self):
        if not self.video_path: return
        a = self.range_slider.lowerValue()
        b = self.range_slider.upperValue()
        if b<=a: QMessageBox.warning(self,"Bad range","End must be after Start."); return
        dst, _ = QFileDialog.getSaveFileName(self, "Save clip",
        self.video_path.with_suffix(".trim.mp4").name,"MP4 Video (*.mp4)")
        if not dst: return
        self.trim_btn.setEnabled(False); self.trim_btn.setText("Trimming…")
        ff=["ffmpeg","-hide_banner","-loglevel","error","-ss",str(a),"-to",str(b),
            "-i",str(self.video_path),"-c","copy",dst]
        def work():
            try: subprocess.run(ff,check=True)
            except subprocess.CalledProcessError as e: QMessageBox.critical(self,"FFmpeg error",str(e))
            else: QMessageBox.information(self,"Saved",f"Clip saved to:\n{dst}")
            finally:
                self.trim_btn.setEnabled(True); self.trim_btn.setText("Trim & Save")
                self.stack.setCurrentIndex(0); self.bar.setValue(0)
                if self.tmp_dir: shutil.rmtree(self.tmp_dir,ignore_errors=True)
        threading.Thread(target=work,daemon=True).start()


# ─────────────────────────── run ──────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_lobster_theme(app)
    apply_custom_cursor(app)      
    main = MainUI()                         # starts hidden / frameless
    splash_png = Path(__file__).with_name("lobster.png")

    # cross-fade helper
    def start_main():
        main.setWindowOpacity(0)
        main.show()

        fade = QPropertyAnimation(main, b"windowOpacity")
        fade.setDuration(600)
        fade.setStartValue(0)
        fade.setEndValue(1)
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        main._fade_in = fade                # keep reference alive

    splash = SplashScreen(splash_png, start_main)
    splash.show()

    sys.exit(app.exec())
