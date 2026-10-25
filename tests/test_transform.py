import pytest

from ledger.transform import Finding, UnsupportedRegionError, transform

REGION = "ap-northeast-1"


def test_transform_single_instance_returns_box_usage_row_for_full_month() -> None:
    # Arrange
    template = {
        "Resources": {
            "WebServer": {
                "Type": "AWS::EC2::Instance",
                "Properties": {"InstanceType": "m7g.large"},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.service_code == "AmazonEC2"
    assert row.usage_type == "APN1-BoxUsage:m7g.large"
    assert row.operation == "RunInstances"
    assert row.amount == 730


def test_transform_instance_row_has_key_from_logical_id_and_readable_label() -> None:
    # Arrange
    template = {
        "Resources": {
            "WebServer": {
                "Type": "AWS::EC2::Instance",
                "Properties": {"InstanceType": "m7g.large"},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    row = result.rows[0]
    assert row.key == "WebServer"
    assert row.label == "WebServer: EC2 m7g.large"


def test_transform_gp3_volume_returns_volume_usage_row_in_gb_month() -> None:
    # Arrange
    template = {
        "Resources": {
            "DataVolume": {
                "Type": "AWS::EC2::Volume",
                "Properties": {"VolumeType": "gp3", "Size": 100, "AvailabilityZone": "x"},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.key == "DataVolume"
    assert row.service_code == "AmazonEC2"
    assert row.usage_type == "APN1-EBS:VolumeUsage.gp3"
    assert row.operation == ""
    assert row.amount == 100


def test_transform_instance_with_block_device_returns_instance_and_ebs_rows() -> None:
    # Arrange
    template = {
        "Resources": {
            "WebServer": {
                "Type": "AWS::EC2::Instance",
                "Properties": {
                    "InstanceType": "m7g.large",
                    "BlockDeviceMappings": [
                        {"DeviceName": "/dev/xvda", "Ebs": {"VolumeType": "gp3", "VolumeSize": 30}}
                    ],
                },
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert [r.usage_type for r in result.rows] == [
        "APN1-BoxUsage:m7g.large",
        "APN1-EBS:VolumeUsage.gp3",
    ]
    ebs = result.rows[1]
    assert ebs.key == "WebServer/dev/xvda"
    assert ebs.operation == ""
    assert ebs.amount == 30


def test_transform_nat_gateway_returns_hourly_row_for_full_month() -> None:
    # Arrange
    template = {
        "Resources": {
            "Nat": {"Type": "AWS::EC2::NatGateway", "Properties": {"SubnetId": "subnet-123"}}
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.key == "Nat"
    assert row.service_code == "AmazonEC2"
    assert row.usage_type == "APN1-NatGateway-Hours"
    assert row.operation == "NatGateway"
    assert row.amount == 730


def test_transform_interface_endpoint_bills_hours_per_subnet() -> None:
    # Arrange
    template = {
        "Resources": {
            "SsmEndpoint": {
                "Type": "AWS::EC2::VPCEndpoint",
                "Properties": {
                    "VpcEndpointType": "Interface",
                    "ServiceName": "com.amazonaws.ap-northeast-1.ssm",
                    "SubnetIds": ["subnet-a", "subnet-c"],
                },
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.key == "SsmEndpoint"
    assert row.service_code == "AmazonVPC"
    assert row.usage_type == "APN1-VpcEndpoint-Hours"
    assert row.operation == "VpcEndpoint"
    assert row.amount == 1460


def test_transform_interface_endpoint_counts_ref_subnets_by_number() -> None:
    # Arrange
    template = {
        "Resources": {
            "SsmEndpoint": {
                "Type": "AWS::EC2::VPCEndpoint",
                "Properties": {
                    "VpcEndpointType": "Interface",
                    "SubnetIds": [{"Ref": "SubnetA"}, {"Ref": "SubnetC"}, {"Ref": "SubnetD"}],
                },
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows[0].amount == 2190


def test_transform_multiple_resources_returns_all_rows() -> None:
    # Arrange
    template = {
        "Resources": {
            "WebServer": {
                "Type": "AWS::EC2::Instance",
                "Properties": {"InstanceType": "m7g.large"},
            },
            "Nat": {"Type": "AWS::EC2::NatGateway", "Properties": {}},
            "DataVolume": {
                "Type": "AWS::EC2::Volume",
                "Properties": {"VolumeType": "gp2", "Size": 50},
            },
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert [r.key for r in result.rows] == ["WebServer", "Nat", "DataVolume"]


def test_transform_unsupported_type_is_listed_as_excluded_with_reason() -> None:
    # Arrange
    template = {
        "Resources": {
            "Handler": {"Type": "AWS::Lambda::Function", "Properties": {"Runtime": "python3.12"}}
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.logical_id == "Handler"
    assert finding.resource_type == "AWS::Lambda::Function"
    assert finding.kind == "excluded"
    assert finding.reason == "最小版の対象外"


def test_transform_cdk_metadata_and_non_resource_sections_are_ignored() -> None:
    # Arrange
    template = {
        "Parameters": {"BootstrapVersion": {"Type": "AWS::SSM::Parameter::Value<String>"}},
        "Rules": {"CheckBootstrapVersion": {"Assertions": []}},
        "Conditions": {"IsProd": {"Fn::Equals": ["a", "b"]}},
        "Outputs": {"Url": {"Value": "x"}},
        "Metadata": {"Note": "x"},
        "Resources": {
            "CDKMetadata": {"Type": "AWS::CDK::Metadata", "Properties": {"Analytics": "v2:x"}}
        },
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert result.findings == []


def test_transform_instance_type_intrinsic_is_excluded_without_row() -> None:
    # Arrange
    template = {
        "Resources": {
            "WebServer": {
                "Type": "AWS::EC2::Instance",
                "Properties": {"InstanceType": {"Ref": "InstanceTypeParam"}},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert result.findings == [
        Finding(
            "WebServer",
            "AWS::EC2::Instance",
            "excluded",
            "InstanceType が値ではない（組み込み関数）",
        )
    ]


def test_transform_volume_with_intrinsic_size_or_type_is_excluded() -> None:
    # Arrange
    template = {
        "Resources": {
            "SizeRef": {
                "Type": "AWS::EC2::Volume",
                "Properties": {"VolumeType": "gp3", "Size": {"Ref": "SizeParam"}},
            },
            "TypeIf": {
                "Type": "AWS::EC2::Volume",
                "Properties": {"VolumeType": {"Fn::If": ["IsProd", "io2", "gp3"]}, "Size": 10},
            },
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert result.findings == [
        Finding("SizeRef", "AWS::EC2::Volume", "excluded", "Size が値ではない（組み込み関数）"),
        Finding(
            "TypeIf", "AWS::EC2::Volume", "excluded", "VolumeType が値ではない（組み込み関数）"
        ),
    ]


def test_transform_endpoint_without_type_is_excluded_as_gateway_default() -> None:
    # Arrange
    template = {
        "Resources": {
            "S3Endpoint": {
                "Type": "AWS::EC2::VPCEndpoint",
                "Properties": {"ServiceName": "com.amazonaws.ap-northeast-1.s3"},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert result.findings == [
        Finding(
            "S3Endpoint",
            "AWS::EC2::VPCEndpoint",
            "excluded",
            "Gateway 型（VpcEndpointType の既定値）は時間の課金がない",
        )
    ]


def test_transform_gateway_endpoint_is_excluded() -> None:
    # Arrange
    template = {
        "Resources": {
            "S3Endpoint": {
                "Type": "AWS::EC2::VPCEndpoint",
                "Properties": {"VpcEndpointType": "Gateway", "RouteTableIds": ["rtb-1"]},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert [f.kind for f in result.findings] == ["excluded"]


def test_transform_dedicated_tenancy_instance_is_excluded() -> None:
    # Arrange
    template = {
        "Resources": {
            "Dedicated": {
                "Type": "AWS::EC2::Instance",
                "Properties": {"InstanceType": "m7g.large", "Tenancy": "dedicated"},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert result.findings == [
        Finding("Dedicated", "AWS::EC2::Instance", "excluded", "共有テナンシー以外は最小版の対象外")
    ]


def test_transform_instance_without_instance_type_is_excluded() -> None:
    # Arrange
    template = {
        "Resources": {
            "FromLaunchTemplate": {
                "Type": "AWS::EC2::Instance",
                "Properties": {"LaunchTemplate": {"LaunchTemplateId": "lt-1", "Version": "1"}},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert result.findings == [
        Finding(
            "FromLaunchTemplate",
            "AWS::EC2::Instance",
            "excluded",
            "InstanceType がない（LaunchTemplate 経由など）",
        )
    ]


def test_transform_instance_without_block_devices_excludes_root_volume_only() -> None:
    # Arrange
    template = {
        "Resources": {
            "WebServer": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "m7g.large"}}
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert [r.key for r in result.rows] == ["WebServer"]
    assert result.findings == [
        Finding(
            "WebServer",
            "AWS::EC2::Instance",
            "excluded",
            "ルートボリュームは AMI で決まるため見積もれない（BlockDeviceMappings がない）",
        )
    ]


def test_transform_block_device_without_volume_type_is_excluded() -> None:
    # Arrange
    template = {
        "Resources": {
            "WebServer": {
                "Type": "AWS::EC2::Instance",
                "Properties": {
                    "InstanceType": "m7g.large",
                    "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 30}}],
                },
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert [r.key for r in result.rows] == ["WebServer"]
    assert result.findings == [
        Finding(
            "WebServer/dev/xvda",
            "AWS::EC2::Instance",
            "excluded",
            "VolumeType が省略されている（既定値は AMI 依存）",
        )
    ]


def test_transform_volume_with_provisioned_iops_keeps_row_and_adds_note() -> None:
    # Arrange
    template = {
        "Resources": {
            "DataVolume": {
                "Type": "AWS::EC2::Volume",
                "Properties": {"VolumeType": "gp3", "Size": 100, "Iops": 6000, "Throughput": 250},
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert [r.key for r in result.rows] == ["DataVolume"]
    assert result.findings == [
        Finding(
            "DataVolume",
            "AWS::EC2::Volume",
            "note",
            "IOPS・スループットの追加課金は見積もりに含まない",
        )
    ]


def test_transform_conditional_resource_keeps_rows_and_adds_note() -> None:
    # Arrange
    template = {
        "Resources": {
            "Nat": {"Type": "AWS::EC2::NatGateway", "Condition": "IsProd", "Properties": {}}
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert [r.key for r in result.rows] == ["Nat"]
    assert result.findings == [
        Finding(
            "Nat",
            "AWS::EC2::NatGateway",
            "note",
            "条件付きのリソース（IsProd）。作られるものとして見積もった",
        )
    ]


def test_transform_unsupported_region_raises_error_naming_region() -> None:
    # Arrange
    template: dict[str, object] = {"Resources": {}}

    # Act / Assert
    with pytest.raises(UnsupportedRegionError, match="us-west-2"):
        transform(template, "us-west-2")


def test_transform_template_without_resources_returns_nothing() -> None:
    # Arrange
    template: dict[str, object] = {"Outputs": {}}

    # Act
    result = transform(template, REGION)

    # Assert
    assert result.rows == []
    assert result.findings == []


def test_transform_excluded_and_notes_share_one_list_with_kind() -> None:
    # Arrange
    template = {
        "Resources": {
            "Handler": {"Type": "AWS::Lambda::Function", "Properties": {}},
            "Nat": {"Type": "AWS::EC2::NatGateway", "Condition": "IsProd", "Properties": {}},
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert [(f.logical_id, f.kind) for f in result.findings] == [
        ("Handler", "excluded"),
        ("Nat", "note"),
    ]


def test_transform_block_device_with_provisioned_iops_keeps_row_and_adds_note() -> None:
    # Arrange
    template = {
        "Resources": {
            "WebServer": {
                "Type": "AWS::EC2::Instance",
                "Properties": {
                    "InstanceType": "m7g.large",
                    "BlockDeviceMappings": [
                        {
                            "DeviceName": "/dev/xvda",
                            "Ebs": {"VolumeType": "gp3", "VolumeSize": 30, "Iops": 4000},
                        }
                    ],
                },
            }
        }
    }

    # Act
    result = transform(template, REGION)

    # Assert
    assert [r.key for r in result.rows] == ["WebServer", "WebServer/dev/xvda"]
    assert result.findings == [
        Finding(
            "WebServer/dev/xvda",
            "AWS::EC2::Instance",
            "note",
            "IOPS・スループットの追加課金は見積もりに含まない",
        )
    ]
