"""
Qt compatibility helpers.

Imports the Qt classes the app uses through pyqtgraph, so it runs on PySide6
or PyQt, and resolves enums whose location differs between Qt5 and Qt6.
"""

from pyqtgraph.Qt import QT_LIB, QtCore, QtGui, QtWidgets


def enum_of(owner, scope: str, name: str):
    """Fetch an enum member from Qt5 (flat) or Qt6 (scoped) bindings.

    enum_of(QSizePolicy, "Policy", "Expanding") returns
    QSizePolicy.Policy.Expanding on Qt6 and QSizePolicy.Expanding on Qt5.
    """
    holder = getattr(owner, scope, None)
    if holder is not None and hasattr(holder, name):
        return getattr(holder, name)
    return getattr(owner, name)


Qt = QtCore.Qt
Signal = QtCore.Signal
QThread = QtCore.QThread
QTimer = QtCore.QTimer
QKeySequence = QtGui.QKeySequence

# Qt6 moved these out of QtWidgets into QtGui.
QShortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut

QApplication = QtWidgets.QApplication
QFrame = QtWidgets.QFrame
QGridLayout = QtWidgets.QGridLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QPushButton = QtWidgets.QPushButton
QSizePolicy = QtWidgets.QSizePolicy
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget

ALIGN_BOTTOM = enum_of(Qt, "AlignmentFlag", "AlignBottom")
ALIGN_LEFT = enum_of(Qt, "AlignmentFlag", "AlignLeft")
ALIGN_VCENTER = enum_of(Qt, "AlignmentFlag", "AlignVCenter")
SIZE_EXPANDING = enum_of(QSizePolicy, "Policy", "Expanding")
SIZE_FIXED = enum_of(QSizePolicy, "Policy", "Fixed")
SIZE_PREFERRED = enum_of(QSizePolicy, "Policy", "Preferred")

__all__ = [
    "QT_LIB", "QtCore", "QtGui", "QtWidgets", "enum_of",
    "Qt", "Signal", "QThread", "QTimer", "QKeySequence", "QShortcut",
    "QApplication", "QFrame", "QGridLayout", "QHBoxLayout", "QLabel",
    "QPushButton", "QSizePolicy", "QVBoxLayout", "QWidget",
    "ALIGN_BOTTOM", "ALIGN_LEFT", "ALIGN_VCENTER",
    "SIZE_EXPANDING", "SIZE_FIXED", "SIZE_PREFERRED",
]
