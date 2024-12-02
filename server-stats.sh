#!/bin/bash

# Function to display CPU usage
cpu_usage() {
    echo "CPU Usage:"
    top -bn1 | grep "Cpu(s)" | \
    awk '{print "User: "$2"%, System: "$4"%, Idle: "$8"%"}'
    echo
}

# Function to display memory usage
memory_usage() {
    echo "Memory Usage:"
    free -h | awk 'NR==2{printf "Used: %s, Free: %s, Usage: %.2f%%\n", $3, $4, $3/$2*100}'
    echo
}

# Function to display disk usage
disk_usage() {
    echo "Disk Usage:"
    df -h --total | awk 'END{printf "Used: %s, Available: %s, Usage: %s\n", $3, $4, $5}'
    echo
}

# Function to display top 5 processes by CPU usage
top_cpu_processes() {
    echo "Top 5 Processes by CPU Usage:"
    ps -eo pid,comm,%cpu --sort=-%cpu | head -n 6
    echo
}

# Function to display top 5 processes by memory usage
top_memory_processes() {
    echo "Top 5 Processes by Memory Usage:"
    ps -eo pid,comm,%mem --sort=-%mem | head -n 6
    echo
}

# Optional: Display additional stats
additional_stats() {
    echo "Additional Stats:"
    echo "OS Version: $(cat /etc/os-release | grep PRETTY_NAME | cut -d '"' -f 2)"
    echo "Uptime: $(uptime -p)"
    echo "Load Average: $(uptime | awk -F 'load average: ' '{print $2}')"
    echo "Logged In Users: $(who | wc -l)"
    echo "Failed Login Attempts:"
    sudo grep "Failed password" /var/log/auth.log | wc -l
    echo
}

# Main Script
echo "Server Performance Stats"
echo "========================="
cpu_usage
memory_usage
disk_usage
top_cpu_processes
top_memory_processes
additional_stats
