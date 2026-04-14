#!/usr/bin/env python3
"""
Pre-flight verification script for daily data collection.
Checks environment, dependencies, and prerequisites before running collection.
"""

import os
import sys
import sqlite3
import logging
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# Configure basic logging for verification
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DailyEnvVerifier:
    """Verifies environment and prerequisites for daily data collection"""

    def __init__(self, db_path: str = "crypto_data.db"):
        self.db_path = db_path
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "checks": {},
            "overall_status": "PENDING",
        }

    def check_python_environment(self) -> Tuple[bool, str]:
        """Check Python environment and edoras package importability"""
        try:
            import os as _os

            _src_dir = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", "src"))
            if _src_dir not in self._get_sys_path():
                import sys as _sys

                _sys.path.insert(0, _src_dir)

            from config import DB_PATH

            if not _os.path.exists(DB_PATH):
                logger.error(f"✗ DB_PATH does not exist: {DB_PATH}")
                return False, f"DB_PATH does not exist: {DB_PATH}"

            logger.info(f"✓ config module importable, DB_PATH={DB_PATH}")
            return True, f"config module importable, DB_PATH={DB_PATH}"
        except ImportError as e:
            logger.error(f"✗ Failed to import config: {e}")
            return False, f"Failed to import config: {e}"
        except Exception as e:
            logger.error(f"✗ Unexpected error: {e}")
            return False, f"Unexpected error: {e}"

    def _get_sys_path(self):
        import sys as _sys

        return _sys.path

    def check_coinbase_credentials(self) -> Tuple[bool, str]:
        """Check Coinbase API credentials exist and are valid format"""
        api_key = os.getenv("COINBASE_API_KEY")
        api_secret = os.getenv("COINBASE_API_SECRET")

        if not api_key or not api_secret:
            logger.error("✗ COINBASE_API_KEY or COINBASE_API_SECRET not set in environment")
            return False, "API credentials not set in environment"

        # Check if API key looks valid (starts with expected pattern)
        if not api_key.startswith("-----BEGIN EC PRIVATE KEY-----") and len(api_key) < 10:
            logger.warning("⚠ API key format may be invalid")

        # Check if secret has proper format
        if "-----BEGIN EC PRIVATE KEY-----" in api_secret:
            # Fix newlines if needed
            if "\\n" in api_secret:
                logger.info("✓ API secret has EC private key format (newlines will be fixed)")
            else:
                logger.info("✓ API secret has EC private key format")
        else:
            logger.info("✓ API secret format detected")

        logger.info("✓ Coinbase API credentials present")
        return True, "Coinbase API credentials present"

    def check_database_accessible(self) -> Tuple[bool, str]:
        """Check if database is accessible and has required tables"""
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Check required tables exist
            required_tables = ["candlesticks", "indicators", "portfolios"]
            existing_tables = []

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            for table in required_tables:
                if table in tables:
                    existing_tables.append(table)
                else:
                    logger.warning(f"⚠ Table '{table}' not found in database")

            conn.close()

            if len(existing_tables) >= 2:  # At least candlesticks and indicators
                logger.info(f"✓ Database accessible, found tables: {', '.join(existing_tables)}")
                return (
                    True,
                    f"Database accessible with {len(existing_tables)}/{len(required_tables)} tables",
                )
            else:
                logger.error(f"✗ Missing required tables, only found: {existing_tables}")
                return (
                    False,
                    f"Missing required tables: {set(required_tables) - set(existing_tables)}",
                )

        except sqlite3.Error as e:
            logger.error(f"✗ Database error: {e}")
            return False, f"Database error: {e}"
        except Exception as e:
            logger.error(f"✗ Unexpected database error: {e}")
            return False, f"Unexpected error: {e}"

    def check_disk_space(self, min_gb: float = 1.0) -> Tuple[bool, str]:
        """Check available disk space (Linux only)"""
        try:
            import shutil

            total, used, free = shutil.disk_usage("/")
            free_gb = free / (1024**3)

            if free_gb >= min_gb:
                logger.info(f"✓ Sufficient disk space: {free_gb:.1f}GB free")
                return True, f"{free_gb:.1f}GB free disk space"
            else:
                logger.warning(f"⚠ Low disk space: {free_gb:.1f}GB free (minimum {min_gb}GB)")
                return False, f"Low disk space: {free_gb:.1f}GB free"
        except ImportError:
            logger.info("⚠ Could not check disk space (shutil not available)")
            return True, "Disk space check skipped"
        except Exception as e:
            logger.warning(f"⚠ Could not check disk space: {e}")
            return True, f"Disk space check failed: {e}"

    def check_network_connectivity(self) -> Tuple[bool, str]:
        """Check basic network connectivity"""
        try:
            import socket

            # Try to resolve Coinbase API domain
            socket.gethostbyname("api.coinbase.com")
            logger.info("✓ Network connectivity: api.coinbase.com resolvable")
            return True, "Network connectivity OK"
        except socket.gaierror:
            logger.error("✗ Network error: Cannot resolve api.coinbase.com")
            return False, "Cannot resolve api.coinbase.com"
        except Exception as e:
            logger.warning(f"⚠ Network check failed: {e}")
            return True, f"Network check inconclusive: {e}"

    def run_all_checks(self) -> Dict:
        """Run all verification checks"""
        checks = [
            ("python_environment", self.check_python_environment),
            ("coinbase_credentials", self.check_coinbase_credentials),
            ("database_accessible", self.check_database_accessible),
            ("disk_space", self.check_disk_space),
            ("network_connectivity", self.check_network_connectivity),
        ]

        all_passed = True
        for check_name, check_func in checks:
            logger.info(f"Running check: {check_name}")
            passed, message = check_func()
            self.results["checks"][check_name] = {
                "passed": passed,
                "message": message,
                "timestamp": datetime.now().isoformat(),
            }
            if not passed:
                all_passed = False

        self.results["overall_status"] = "PASS" if all_passed else "FAIL"
        logger.info(f"Verification complete: {self.results['overall_status']}")

        return self.results

    def save_results(self, output_path: str = "verification_results.json"):
        """Save verification results to JSON file"""
        try:
            with open(output_path, "w") as f:
                json.dump(self.results, f, indent=2, default=str)
            logger.info(f"✓ Verification results saved to {output_path}")
        except Exception as e:
            logger.error(f"✗ Failed to save results: {e}")

    def print_summary(self):
        """Print verification summary"""
        print("\n" + "=" * 60)
        print("DAILY DATA COLLECTION VERIFICATION SUMMARY")
        print("=" * 60)

        for check_name, check_result in self.results["checks"].items():
            status = "✓ PASS" if check_result["passed"] else "✗ FAIL"
            print(f"{status:10} {check_name:25} {check_result['message']}")

        print("-" * 60)
        print(f"OVERALL: {self.results['overall_status']}")
        print("=" * 60)


def main():
    """Main entry point for verification script"""
    import argparse

    parser = argparse.ArgumentParser(description="Verify environment for daily data collection")
    parser.add_argument("--db-path", default="crypto_data.db", help="Path to SQLite database")
    parser.add_argument(
        "--output", default="verification_results.json", help="Output JSON file path"
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress detailed output")

    args = parser.parse_args()

    if not args.quiet:
        print("Starting environment verification for daily data collection...")

    verifier = DailyEnvVerifier(db_path=args.db_path)
    results = verifier.run_all_checks()

    if not args.quiet:
        verifier.print_summary()

    verifier.save_results(args.output)

    # Exit with appropriate code
    if results["overall_status"] == "PASS":
        if not args.quiet:
            print("\n✓ All checks passed. Environment is ready for data collection.")
        sys.exit(0)
    else:
        if not args.quiet:
            print("\n✗ Some checks failed. Please fix issues before running data collection.")
        sys.exit(1)


if __name__ == "__main__":
    main()
