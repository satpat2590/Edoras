#!/bin/bash
# Build script for edoras-dashboard PyInstaller binary
# Usage: ./build-dashboard.sh [--install] [--clean]
#   --install: Install PyInstaller if not present
#   --clean: Remove build artifacts before building

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_PY="$PROJECT_DIR/dashboard.py"
BINARY_NAME="edoras-dashboard"
INSTALL_DIR="$HOME/.local/bin"
PYINSTALLER_SPEC="$PROJECT_DIR/$BINARY_NAME.spec"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_prerequisites() {
    if ! command -v python3 &> /dev/null; then
        error "python3 not found in PATH"
        exit 1
    fi
    
    if [[ ! -f "$DASHBOARD_PY" ]]; then
        error "dashboard.py not found at $DASHBOARD_PY"
        exit 1
    fi
}

install_pyinstaller() {
    if ! python3 -c "import PyInstaller" 2>/dev/null; then
        info "Installing PyInstaller..."
        python3 -m pip install --user pyinstaller
    else
        info "PyInstaller already installed"
    fi
}

clean_build() {
    info "Cleaning previous build artifacts..."
    rm -rf "$PROJECT_DIR/build" "$PROJECT_DIR/dist" 2>/dev/null || true
    rm -f "$PYINSTALLER_SPEC" 2>/dev/null || true
}

build_binary() {
    info "Building $BINARY_NAME binary..."
    
    # Build with PyInstaller
    cd "$PROJECT_DIR"
    python3 -m PyInstaller --onefile \
        --name "$BINARY_NAME" \
        --hidden-import="sqlite3" \
        --hidden-import="PIL" \
        --hidden-import="rich" \
        --hidden-import="numpy" \
        --hidden-import="pandas" \
        --hidden-import="yfinance" \
        --hidden-import="markdown_it" \
        --hidden-import="pygments" \
        --add-data="config.py:." \
        "$DASHBOARD_PY"
    
    # Check if binary was created
    if [[ ! -f "$PROJECT_DIR/dist/$BINARY_NAME" ]]; then
        error "Binary not created at $PROJECT_DIR/dist/$BINARY_NAME"
        exit 1
    fi
    
    info "Binary created: $PROJECT_DIR/dist/$BINARY_NAME ($(du -h "$PROJECT_DIR/dist/$BINARY_NAME" | cut -f1))"
}

install_binary() {
    # Ensure install directory exists
    mkdir -p "$INSTALL_DIR"
    
    # Copy binary to ~/.local/bin
    cp "$PROJECT_DIR/dist/$BINARY_NAME" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/$BINARY_NAME"
    
    info "Installed to $INSTALL_DIR/$BINARY_NAME"
    
    # Verify it's on PATH
    if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
        warn "$INSTALL_DIR is not on PATH. Add 'export PATH=\"\$HOME/.local/bin:\$PATH\"' to your shell config"
    fi
}

main() {
    local do_install=false
    local do_clean=false
    
    # Parse arguments
    for arg in "$@"; do
        case "$arg" in
            --install) do_install=true ;;
            --clean) do_clean=true ;;
            --help)
                echo "Usage: $0 [--install] [--clean]"
                echo "  --install: Install PyInstaller if not present"
                echo "  --clean: Remove build artifacts before building"
                exit 0
                ;;
            *)
                error "Unknown argument: $arg"
                exit 1
                ;;
        esac
    done
    
    info "Building edoras-dashboard binary..."
    
    check_prerequisites
    
    if [[ "$do_install" == true ]]; then
        install_pyinstaller
    fi
    
    if [[ "$do_clean" == true ]]; then
        clean_build
    fi
    
    build_binary
    install_binary
    
    info "Build complete! Run 'edoras-dashboard --help' to test."
}

main "$@"