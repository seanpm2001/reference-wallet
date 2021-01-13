# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from http import HTTPStatus

import wallet.services.fund_pull_pre_approval
from diem import offchain as diem_offchain
from diem.offchain import X_REQUEST_ID, X_REQUEST_SENDER_ADDRESS
from flask import Blueprint, request
from flask.views import MethodView
from wallet.services import fund_pull_pre_approval as fppa_service
from wallet.services import offchain as offchain_service
from webapp.routes.strict_schema_view import (
    StrictSchemaView,
    response_definition,
    path_string_param,
    body_parameter,
)
from webapp.schemas import (
    PaymentCommands,
    PaymentCommand,
    FundsPullPreApprovalList,
    ApproveFundsPullPreApproval,
    Error,
    CreateAndApproveFundPullPreApproval,
)

logger = logging.getLogger(__name__)
offchain = Blueprint("offchain", __name__)


class CommandsRoutes:
    @classmethod
    def create_command_response_object(
        cls, approval: fppa_service.models.FundsPullPreApprovalCommand
    ):
        return {
            "address": approval.address,
            "biller_address": approval.biller_address,
            "funds_pull_pre_approval_id": approval.funds_pull_pre_approval_id,
            "scope": {
                "type": approval.funds_pull_pre_approval_type,
                "expiration_time": approval.expiration_timestamp,
                "max_cumulative_amount": {
                    "unit": approval.max_cumulative_unit,
                    "value": approval.max_cumulative_unit_value,
                    "max_amount": {
                        "amount": approval.max_cumulative_amount,
                        "currency": approval.max_cumulative_amount_currency,
                    },
                },
                "max_transaction_amount": {
                    "amount": approval.max_transaction_amount,
                    "currency": approval.max_transaction_amount_currency,
                },
            },
            "description": approval.description,
            "status": approval.status,
        }


def payment_command_to_dict(command: diem_offchain.PaymentCommand):
    payment = command.payment
    payment_dict = {
        "reference_id": command.reference_id(),
        "sender": actor_to_dict(payment.sender),
        "receiver": actor_to_dict(payment.receiver),
        "action": action_to_dict(payment.action),
    }
    if payment.original_payment_reference_id:
        payment_dict[
            "original_payment_reference_id"
        ] = payment.original_payment_reference_id
    if payment.recipient_signature:
        payment_dict["recipient_signature"] = payment.recipient_signature
    if payment.description:
        payment_dict["description"] = payment.description
    payment_command_dict = {
        "my_actor_address": command.my_actor_address,
        "inbound": command.inbound,
        "cid": command.cid,
        "payment": payment_dict,
    }
    return payment_command_dict


def action_to_dict(action):
    return {
        "amount": action.amount,
        "currency": action.currency,
        "action": action.action,
        "timestamp": action.timestamp,
    }


def actor_to_dict(actor):
    actor_dict = {
        "address": actor.address,
        "status": {"status": actor.status.status},
    }
    if actor.metadata:
        actor_dict["metadata"] = actor.metadata
    if actor.additional_kyc_data:
        actor_dict["additional_kyc_data"] = actor.additional_kyc_data
    kyc_data = actor.kyc_data
    if kyc_data:
        actor_dict["kyc_data"] = {
            "type": kyc_data.type,
            "payload_version": kyc_data.payload_version,
            "given_name": kyc_data.given_name,
            "surname": kyc_data.surname,
            "address": kyc_data.address,
            "dob": kyc_data.dob,
            "place_of_birth": kyc_data.place_of_birth,
            "national_id": kyc_data.national_id,
            "legal_entity_name": kyc_data.legal_entity_name,
        }
    return actor_dict


class OffchainRoutes:
    class OffchainView(StrictSchemaView):
        tags = ["Offchain"]

    class GetPaymentCommand(OffchainView):
        summary = "Get Payment Command"

        parameters = [
            path_string_param(
                name="transaction_id", description="transaction internal id"
            )
        ]

        responses = {
            HTTPStatus.OK: response_definition("Payment Command", schema=PaymentCommand)
        }

        def get(self, transaction_id: int):
            payment_command = offchain_service.get_payment_command(transaction_id)

            return (
                payment_command_to_dict(payment_command),
                HTTPStatus.OK,
            )

    class GetAccountPaymentCommands(OffchainView):
        summary = "Get Account Payment Commands"

        responses = {
            HTTPStatus.OK: response_definition(
                "Account Payment Commands", schema=PaymentCommands
            )
        }

        def get(self):
            payment_commands = offchain_service.get_account_payment_commands(
                self.user.account_id
            )

            payments = [
                payment_command_to_dict(payment) for payment in payment_commands
            ]

            return (
                {"payment_commands": payments},
                HTTPStatus.OK,
            )

    class GetFundsPullPreApprovals(OffchainView):
        summary = "Get funds pull pre approvals of a user"

        responses = {
            HTTPStatus.OK: response_definition(
                "Funds pull pre approvals", schema=FundsPullPreApprovalList
            )
        }

        def get(self):
            approvals = fppa_service.get_funds_pull_pre_approvals(self.user.account_id)

            response = [
                CommandsRoutes.create_command_response_object(approval)
                for approval in approvals
            ]

            return (
                {"funds_pull_pre_approvals": response},
                HTTPStatus.OK,
            )

    class UpdateFundPullPreApprovalStatus(OffchainView):
        summary = "Approve or reject pending funds pull pre approval"
        parameters = [
            body_parameter(ApproveFundsPullPreApproval),
            path_string_param(
                name="funds_pull_pre_approval_id",
                description="funds pull pre approval id",
            ),
        ]

        responses = {
            HTTPStatus.NO_CONTENT: response_definition(
                "Request accepted. You should poll for command updates."
            ),
            HTTPStatus.NOT_FOUND: response_definition(
                "Command not found", schema=Error
            ),
        }

        def put(self, funds_pull_pre_approval_id: str):
            params = request.json

            status: str = params["status"]

            try:
                fppa_service.approve(funds_pull_pre_approval_id, status)
            except fppa_service.FundsPullPreApprovalCommandNotFound:
                return self.respond_with_error(
                    HTTPStatus.NOT_FOUND,
                    f"Funds pre approval id {funds_pull_pre_approval_id} was not found.",
                )

            return "OK", HTTPStatus.NO_CONTENT

    class CreateAndApprove(OffchainView):
        summary = "Create and approve fund pull pre approval by payer"
        parameters = [
            body_parameter(CreateAndApproveFundPullPreApproval),
        ]
        responses = {
            HTTPStatus.OK: response_definition(
                "Funds pull pre approvals request successfully sent"
            ),
        }

        def post(self):
            params = request.json

            account_id: int = self.user.account_id
            biller_address: str = params["biller_address"]
            funds_pull_pre_approval_id: str = params["funds_pull_pre_approval_id"]
            scope: dict = params["scope"]
            funds_pull_pre_approval_type: str = scope["type"]
            expiration_timestamp: int = scope["expiration_timestamp"]
            max_cumulative_amount: dict = scope["max_cumulative_amount"]
            max_cumulative_unit: str = max_cumulative_amount["unit"]
            max_cumulative_unit_value: int = max_cumulative_amount["value"]
            max_cumulative_max_amount: dict = max_cumulative_amount["max_amount"]
            max_cumulative_amount: int = max_cumulative_max_amount["amount"]
            max_cumulative_amount_currency: str = max_cumulative_max_amount["currency"]
            max_transaction_amount_object: dict = scope["max_transaction_amount"]
            max_transaction_amount: int = max_transaction_amount_object["amount"]
            max_transaction_amount_currency: str = max_transaction_amount_object[
                "currency"
            ]
            description: str = params["description"]

            fppa_service.create_and_approve(
                account_id=account_id,
                biller_address=biller_address,
                funds_pull_pre_approval_id=funds_pull_pre_approval_id,
                funds_pull_pre_approval_type=funds_pull_pre_approval_type,
                expiration_timestamp=expiration_timestamp,
                max_cumulative_unit=max_cumulative_unit,
                max_cumulative_unit_value=max_cumulative_unit_value,
                max_cumulative_amount=max_cumulative_amount,
                max_cumulative_amount_currency=max_cumulative_amount_currency,
                max_transaction_amount=max_transaction_amount,
                max_transaction_amount_currency=max_transaction_amount_currency,
                description=description,
            )

            return "OK", HTTPStatus.OK

    class OffchainV2View(MethodView):
        def dispatch_request(self, *args, **kwargs):
            x_request_id = request.headers.get(X_REQUEST_ID)
            sender_address = request.headers.get(X_REQUEST_SENDER_ADDRESS)
            request_body = request.get_data()

            logger.info(f"[{sender_address}:{x_request_id}] offchain v2 income request")

            code, response = offchain_service.process_inbound_command(
                sender_address, request_body
            )

            logger.info(
                f"[{sender_address}:{x_request_id}] response: {code}, {response}"
            )

            return (response, code, {X_REQUEST_ID: x_request_id})
