#!/usr/bin/env python3
"""
Schedule random daily portfolio reports.
Run this script once per day (e.g., at 3 AM) to schedule random report times for the day.
"""

import os
import sys
import random
import subprocess
from datetime import datetime, timedelta
import json

class ReportScheduler:
    """Schedule random portfolio reports throughout the day"""
    
    def __init__(self, config_file: str = "report_schedule.json"):
        self.config_file = config_file
        self.config = self.load_config()
        self.today = datetime.now()
        self.scheduled_times = []
    
    def load_config(self) -> dict:
        """Load scheduler configuration"""
        default_config = {
            "min_reports_per_day": 1,
            "max_reports_per_day": 3,
            "start_hour": 8,    # 8 AM
            "end_hour": 20,     # 8 PM
            "min_interval_hours": 2,  # Minimum 2 hours between reports
            "script_path": os.path.join(os.path.dirname(__file__), "run_portfolio_report.sh"),
            "log_file": os.path.join(os.path.dirname(__file__), "report_schedule.log")
        }
        
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    user_config = json.load(f)
                    default_config.update(user_config)
        except Exception as e:
            print(f"Warning: Could not load config file: {e}")
        
        return default_config
    
    def generate_random_times(self) -> list:
        """Generate random report times for today"""
        config = self.config
        
        # Determine how many reports today
        num_reports = random.randint(
            config["min_reports_per_day"],
            config["max_reports_per_day"]
        )
        
        print(f"Scheduling {num_reports} random report(s) for today")
        
        times = []
        
        for i in range(num_reports):
            # Keep trying until we find a valid time
            max_attempts = 20
            for attempt in range(max_attempts):
                # Generate random hour and minute
                hour = random.randint(config["start_hour"], config["end_hour"] - 1)
                minute = random.randint(0, 59)
                
                candidate_time = self.today.replace(hour=hour, minute=minute, second=0, microsecond=0)
                
                # Check if this time is valid (not too close to other times)
                valid = True
                for existing_time in times:
                    time_diff = abs((candidate_time - existing_time).total_seconds() / 3600)
                    if time_diff < config["min_interval_hours"]:
                        valid = False
                        break
                
                if valid:
                    times.append(candidate_time)
                    break
                elif attempt == max_attempts - 1:
                    # Couldn't find valid time, use any time
                    print(f"Warning: Could not find well-spaced time for report {i+1}, using any time")
                    times.append(candidate_time)
        
        # Sort times chronologically
        times.sort()
        return times
    
    def schedule_with_at(self, report_time: datetime) -> bool:
        """Schedule a report using the 'at' command"""
        config = self.config
        
        # Format time for at command
        # at accepts times like "3:30 PM" or "15:30"
        time_str = report_time.strftime("%I:%M %p").lstrip('0')  # e.g., "3:30 PM"
        
        # Build the at command
        script_path = config["script_path"]
        
        if not os.path.exists(script_path):
            print(f"Error: Script not found at {script_path}")
            return False
        
        # Create at command
        at_command = f'echo "{script_path}" | at {time_str} today 2>&1'
        
        try:
            result = subprocess.run(at_command, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                # Parse at job ID from output
                # at output is like "job 42 at Thu Mar  9 15:30:00 2026"
                output = result.stdout.strip()
                job_id = None
                for line in output.split('\n'):
                    if line.startswith('job'):
                        parts = line.split()
                        if len(parts) >= 2:
                            job_id = parts[1]
                            break
                
                print(f"  Scheduled report at {time_str} (job {job_id})")
                return True
            else:
                print(f"  Error scheduling at {time_str}: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"  Exception scheduling at {time_str}: {e}")
            return False
    
    def log_schedule(self, times: list):
        """Log the scheduled times to file"""
        config = self.config
        
        log_entry = {
            'date': self.today.strftime('%Y-%m-%d'),
            'scheduled_at': datetime.now().isoformat(),
            'report_times': [t.strftime('%H:%M') for t in times],
            'report_count': len(times)
        }
        
        try:
            # Read existing log
            logs = []
            if os.path.exists(config["log_file"]):
                with open(config["log_file"], 'r') as f:
                    try:
                        logs = json.load(f)
                    except:
                        logs = []
            
            # Add new entry
            logs.append(log_entry)
            
            # Keep only last 30 days
            if len(logs) > 30:
                logs = logs[-30:]
            
            # Write back
            with open(config["log_file"], 'w') as f:
                json.dump(logs, f, indent=2)
            
            print(f"Schedule logged to {config['log_file']}")
            
        except Exception as e:
            print(f"Warning: Could not log schedule: {e}")
    
    def clear_old_at_jobs(self):
        """Clear any existing at jobs from previous runs"""
        try:
            # List current at jobs
            result = subprocess.run("atq", shell=True, capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout.strip():
                print("Clearing existing at jobs...")
                # Get job IDs
                job_ids = []
                for line in result.stdout.strip().split('\n'):
                    if line:
                        job_id = line.split()[0]
                        job_ids.append(job_id)
                
                # Remove each job
                for job_id in job_ids:
                    subprocess.run(f"atrm {job_id}", shell=True, capture_output=True)
                
                print(f"Cleared {len(job_ids)} existing at job(s)")
                
        except Exception as e:
            print(f"Warning: Could not clear old at jobs: {e}")
    
    def schedule_daily_reports(self):
        """Main scheduling function"""
        print(f"Daily Report Scheduler - {self.today.strftime('%Y-%m-%d')}")
        print("-" * 50)
        
        # Clear any old at jobs first
        self.clear_old_at_jobs()
        
        # Generate random times
        times = self.generate_random_times()
        
        if not times:
            print("No report times generated")
            return False
        
        print(f"\nGenerated {len(times)} report time(s):")
        for i, t in enumerate(times, 1):
            print(f"  {i}. {t.strftime('%I:%M %p')}")
        
        # Schedule each time with at
        print(f"\nScheduling reports with 'at' command...")
        successes = 0
        
        for t in times:
            if self.schedule_with_at(t):
                successes += 1
        
        # Log the schedule
        self.log_schedule(times)
        
        print(f"\nScheduled {successes} out of {len(times)} report(s) successfully")
        
        if successes > 0:
            # Also create a cron-style schedule file for reference
            cron_file = os.path.join(os.path.dirname(__file__), "today_schedule.txt")
            with open(cron_file, 'w') as f:
                f.write(f"# Today's random report schedule - {self.today.strftime('%Y-%m-%d')}\n")
                for t in times:
                    f.write(f"{t.strftime('%H %M * * *')} {self.config['script_path']}\n")
            
            print(f"Schedule saved to {cron_file}")
            return True
        else:
            print("ERROR: Failed to schedule any reports")
            return False


def main():
    """Main execution function"""
    print("Coinbase Portfolio Report Scheduler")
    print("=" * 50)
    
    # Get script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Initialize scheduler
    scheduler = ReportScheduler()
    
    # Schedule reports
    success = scheduler.schedule_daily_reports()
    
    if success:
        print("\n✅ Daily reports scheduled successfully!")
        print("\nToday's reports will run at the randomly generated times.")
        print("Reports will be sent to your Telegram (personal chat).")
    else:
        print("\n❌ Failed to schedule reports")
        sys.exit(1)


if __name__ == "__main__":
    main()