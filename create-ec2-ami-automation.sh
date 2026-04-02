#!/bin/bash

set -euo pipefail

# --- INPUT ---
read -p "Enter AWS Region (e.g., ap-south-1): " AWS_REGION
read -p "Enter the EC2 Instance Name: " instance_name
read -p "Enter the AMI Name: " base_ami_name

# --- VALIDATE REGION INPUT ---
if [ -z "$AWS_REGION" ]; then
  echo "❌ Region cannot be empty"
  exit 1
fi

# Optional: Append timestamp for uniqueness
timestamp=$(date +%Y%m%d-%H%M%S)
ami_name="${base_ami_name}-${timestamp}"

echo "🔍 Resolving instance ID for: $instance_name in region $AWS_REGION"

# --- FETCH INSTANCE ID ---
instance_id=$(aws ec2 describe-instances \
  --region "$AWS_REGION" \
  --filters "Name=tag:Name,Values=$instance_name" "Name=instance-state-name,Values=running,stopped" \
  --query 'Reservations[].Instances[].InstanceId' \
  --output text)

# --- VALIDATION ---
if [ -z "$instance_id" ]; then
  echo "❌ No instance found with name: $instance_name in region $AWS_REGION"
  exit 1
fi

count=$(echo "$instance_id" | wc -w)
if [ "$count" -gt 1 ]; then
  echo "❌ Multiple instances found with same name. Please refine your tag."
  echo "Instances: $instance_id"
  exit 1
fi

echo "✅ Found instance: $instance_id"

# --- CREATE AMI (NO REBOOT) ---
echo "🚀 Creating AMI (no reboot)..."
ami_id=$(aws ec2 create-image \
  --region "$AWS_REGION" \
  --instance-id "$instance_id" \
  --name "$ami_name" \
  --no-reboot \
  --query 'ImageId' \
  --output text)

echo "📦 AMI creation started: $ami_id"

# --- TAG AMI ---
echo "🏷️ Tagging AMI..."
aws ec2 create-tags \
  --region "$AWS_REGION" \
  --resources "$ami_id" \
  --tags Key=Name,Value="$ami_name"

# --- WAIT FOR AMI ---
echo "⏳ Waiting for AMI to become available..."
aws ec2 wait image-available \
  --region "$AWS_REGION" \
  --image-ids "$ami_id"

echo "✅ AMI is now available"

# --- FETCH SNAPSHOT IDS ---
echo "🔍 Retrieving snapshot IDs..."
snapshot_ids=$(aws ec2 describe-images \
  --region "$AWS_REGION" \
  --image-ids "$ami_id" \
  --query 'Images[].BlockDeviceMappings[].Ebs.SnapshotId' \
  --output text)

if [ -z "$snapshot_ids" ]; then
  echo "❌ No snapshots found for AMI: $ami_id"
  exit 1
fi

# --- TAG SNAPSHOTS ---
echo "🏷️ Tagging snapshots..."
for snapshot_id in $snapshot_ids; do
  echo "Tagging snapshot: $snapshot_id"
  aws ec2 create-tags \
    --region "$AWS_REGION" \
    --resources "$snapshot_id" \
    --tags Key=Name,Value="$ami_name"
done

echo "🎉 SUCCESS: AMI and snapshots created without reboot"
echo "AMI ID: $ami_id"
echo "Region: $AWS_REGION"
echo "Name: $ami_name"
