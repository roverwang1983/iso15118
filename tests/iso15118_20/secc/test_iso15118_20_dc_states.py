from typing import Type
from unittest.mock import AsyncMock, Mock, patch

import pytest

from iso15118.secc.comm_session_handler import SECCCommunicationSession
from iso15118.secc.controller.ev_data import (
    EVDataContext,
    EVDCCLLimits,
    EVDCCPDLimits,
    EVRatedLimits,
    EVSessionContext,
)
from iso15118.secc.controller.evse_data import (
    EVSEDataContext,
    EVSEDCBPTCPDLimits,
    EVSEDCCLLimits,
    EVSEDCCPDLimits,
    EVSERatedLimits,
    EVSESessionContext,
)
from iso15118.secc.controller.simulator import SimEVSEController
from iso15118.secc.failed_responses import init_failed_responses_iso_v20
from iso15118.secc.states.iso15118_20_states import (
    DCCableCheck,
    DCChargeLoop,
    DCChargeParameterDiscovery,
    DCPreCharge,
    PowerDelivery,
    ScheduleExchange,
)
from iso15118.shared.messages.enums import (
    ControlMode,
    CpState,
    EnergyTransferModeEnum,
    IsolationLevel,
    Protocol,
    ServiceV20,
)
from iso15118.shared.messages.iso15118_20.common_messages import (
    ChargeProgress,
    SelectedEnergyService,
)
from iso15118.shared.messages.iso15118_20.common_types import Processing, RationalNumber
from iso15118.shared.messages.iso15118_20.dc import (
    BPTDCChargeParameterDiscoveryReqParams,
    BPTDCChargeParameterDiscoveryResParams,
    BPTDynamicDCChargeLoopReqParams,
    BPTDynamicDCChargeLoopRes,
    BPTScheduledDCChargeLoopReqParams,
    BPTScheduledDCChargeLoopResParams,
    DCChargeLoopRes,
    DCChargeParameterDiscoveryReqParams,
    DCChargeParameterDiscoveryRes,
    DCChargeParameterDiscoveryResParams,
    DynamicDCChargeLoopReqParams,
    DynamicDCChargeLoopRes,
    ScheduledDCChargeLoopReqParams,
    ScheduledDCChargeLoopResParams,
)
from iso15118.shared.notifications import StopNotification
from iso15118.shared.settings import load_shared_settings
from iso15118.shared.states import State, Terminate
from tests.dinspec.secc.test_dinspec_secc_states import MockWriter
from tests.iso15118_20.secc.test_messages import (
    get_cable_check_req,
    get_dc_charge_loop_req,
    get_dc_service_discovery_req,
    get_power_delivery_req,
    get_precharge_req,
    get_schedule_exchange_req_message,
    get_v2g_message_dc_charge_parameter_discovery_req,
)


@patch("iso15118.shared.states.EXI.to_exi", new=Mock(return_value=b"01"))
@pytest.mark.asyncio
class TestEvScenarios:
    @pytest.fixture(autouse=True)
    def _comm_session(self):
        self.comm_session = Mock(spec=SECCCommunicationSession)
        self.comm_session.session_id = "F9F9EE8505F55838"
        self.comm_session.selected_energy_mode = EnergyTransferModeEnum.DC_EXTENDED
        self.comm_session.selected_charging_type_is_ac = False
        self.comm_session.stop_reason = StopNotification(False, "pytest")
        self.comm_session.protocol = Protocol.ISO_15118_20_DC
        self.comm_session.writer = MockWriter()
        self.comm_session.failed_responses_isov20 = init_failed_responses_iso_v20()
        self.comm_session.evse_controller = SimEVSEController()
        self.comm_session.evse_controller.evse_data_context = self.get_evse_data()
        self.comm_session.evse_controller.ev_data_context = EVDataContext(
            ev_rated_limits=EVRatedLimits(dc_limits=EVDCCPDLimits())
        )
        load_shared_settings()

    def get_evse_data(self) -> EVSEDataContext:
        dc_limits = EVSEDCCPDLimits(
            evse_max_charge_power=10,
            evse_min_charge_power=10,
            evse_max_charge_current=10,
            evse_min_charge_current=10,
            evse_max_voltage=10,
            evse_min_voltage=10,
            evse_power_ramp_limit=10,
            # 15118-2 DC, DINSPEC
            evse_current_regulation_tolerance=10,
            evse_peak_current_ripple=10,
            evse_energy_to_be_delivered=10,
        )
        dc_bpt_limits = EVSEDCBPTCPDLimits(
            # 15118-20 DC BPT
            evse_max_discharge_power=10,
            evse_min_discharge_power=10,
            evse_max_discharge_current=10,
            evse_min_discharge_current=10,
        )
        dc_cl_limits = EVSEDCCLLimits(
            # Optional in 15118-20 DC CL (Scheduled)
            evse_max_charge_power=10,
            evse_min_charge_power=10,
            evse_max_charge_current=10,
            evse_max_voltage=10,
            # Optional and present in 15118-20 DC BPT CL (Scheduled)
            evse_max_discharge_power=10,
            evse_min_discharge_power=10,
            evse_max_discharge_current=10,
            evse_min_voltage=10,
        )
        rated_limits: EVSERatedLimits = EVSERatedLimits(
            ac_limits=None, dc_limits=dc_limits, dc_bpt_limits=dc_bpt_limits
        )
        session_context: EVSESessionContext = EVSESessionContext(
            ac_limits=None, dc_limits=dc_cl_limits
        )

        return EVSEDataContext(
            rated_limits=rated_limits, session_context=session_context
        )

    @pytest.mark.parametrize(
        "service_type, dc_params, bpt_params",
        [
            (ServiceV20.DC, "", None),
            (ServiceV20.DC_BPT, None, ""),
        ],
    )
    async def test_15118_20_dc_charge_parameter_discovery_res(
        self, service_type, dc_params, bpt_params
    ):
        self.comm_session.selected_energy_service = SelectedEnergyService(
            service=service_type,
            is_free=True,
            parameter_set=None,
        )
        dc_charge_parameter_discovery = DCChargeParameterDiscovery(self.comm_session)
        await dc_charge_parameter_discovery.process_message(
            message=get_v2g_message_dc_charge_parameter_discovery_req(service_type)
        )
        if service_type == ServiceV20.DC:
            assert bpt_params is None
        elif service_type == ServiceV20.DC_BPT:
            assert dc_params is None
        assert dc_charge_parameter_discovery.next_state is ScheduleExchange

    @pytest.mark.parametrize(
        "control_mode, next_state, selected_energy_service",
        [
            (
                ControlMode.SCHEDULED,
                None,
                SelectedEnergyService(
                    service=ServiceV20.DC, is_free=True, parameter_set=None
                ),
            ),
            (
                ControlMode.DYNAMIC,
                None,
                SelectedEnergyService(
                    service=ServiceV20.DC, is_free=True, parameter_set=None
                ),
            ),
            (
                ControlMode.SCHEDULED,
                None,
                SelectedEnergyService(
                    service=ServiceV20.DC_BPT, is_free=True, parameter_set=None
                ),
            ),
            (
                ControlMode.DYNAMIC,
                None,
                SelectedEnergyService(
                    service=ServiceV20.DC_BPT, is_free=True, parameter_set=None
                ),
            ),
        ],
    )
    async def test_15118_20_schedule_exchange_res(
        self,
        control_mode: ControlMode,
        next_state: Type[State],
        selected_energy_service: SelectedEnergyService,
    ):
        self.comm_session.control_mode = control_mode
        self.comm_session.selected_energy_service = selected_energy_service
        schedule_exchange = ScheduleExchange(self.comm_session)
        await schedule_exchange.process_message(
            message=get_schedule_exchange_req_message(control_mode)
        )
        assert schedule_exchange.next_state is None

    @pytest.mark.parametrize(
        "cable_check_req_received, "
        "is_contactor_closed, "
        "cable_check_status, "
        "expected_state",
        [
            (False, False, None, Terminate),
            (False, True, None, None),
            (False, True, IsolationLevel.VALID, DCPreCharge),
            (True, True, None, None),
            (True, True, IsolationLevel.VALID, DCPreCharge),
        ],
    )
    async def test_15118_20_dc_cable_check(
        self,
        cable_check_req_received: bool,
        is_contactor_closed: bool,
        cable_check_status: IsolationLevel,
        expected_state: Type[State],
    ):
        dc_cable_check = DCCableCheck(self.comm_session)
        dc_cable_check.cable_check_req_was_received = cable_check_req_received
        contactor_status = AsyncMock(return_value=is_contactor_closed)
        self.comm_session.evse_controller.is_contactor_closed = contactor_status
        cable_check_status = AsyncMock(return_value=cable_check_status)
        self.comm_session.evse_controller.get_cable_check_status = cable_check_status
        await dc_cable_check.process_message(message=get_cable_check_req())
        assert dc_cable_check.next_state is expected_state

    @pytest.mark.parametrize(
        "processing, expected_state",
        [(Processing.ONGOING, None), (Processing.FINISHED, PowerDelivery)],
    )
    async def test_15118_20_precharge(
        self, processing: Processing, expected_state: Type[State]
    ):
        dc_pre_charge = DCPreCharge(self.comm_session)
        await dc_pre_charge.process_message(message=get_precharge_req(processing))
        assert dc_pre_charge.next_state is expected_state

    async def test_15118_20_power_delivery(self):
        # TODO
        pass

    @pytest.mark.parametrize(
        "params, selected_service, expected_state, expected_ev_context",
        [
            (
                DCChargeParameterDiscoveryReqParams(
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=0, value=100),
                    ev_max_charge_current=RationalNumber(exponent=0, value=300),
                    ev_min_charge_current=RationalNumber(exponent=0, value=10),
                    ev_max_voltage=RationalNumber(exponent=0, value=1000),
                    ev_min_voltage=RationalNumber(exponent=0, value=10),
                    target_soc=80,
                ),
                ServiceV20.DC,
                ScheduleExchange,
                EVDataContext(
                    ev_rated_limits=EVRatedLimits(
                        dc_limits=EVDCCPDLimits(
                            ev_max_charge_power=30000,
                            ev_min_charge_power=100,
                            ev_max_charge_current=300,
                            ev_min_charge_current=10,
                            ev_max_voltage=1000,
                            ev_min_voltage=10,
                            target_soc=80,
                        )
                    ),
                ),
            ),
            (
                BPTDCChargeParameterDiscoveryReqParams(
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=0, value=100),
                    ev_max_charge_current=RationalNumber(exponent=0, value=300),
                    ev_min_charge_current=RationalNumber(exponent=0, value=10),
                    ev_max_voltage=RationalNumber(exponent=0, value=1000),
                    ev_min_voltage=RationalNumber(exponent=0, value=10),
                    target_soc=80,
                    ev_max_discharge_power=RationalNumber(exponent=0, value=11),
                    ev_min_discharge_power=RationalNumber(exponent=3, value=1),
                    ev_max_discharge_current=RationalNumber(exponent=0, value=11),
                    ev_min_discharge_current=RationalNumber(exponent=0, value=10),
                ),
                ServiceV20.DC_BPT,
                ScheduleExchange,
                EVDataContext(
                    ev_rated_limits=EVRatedLimits(
                        dc_limits=EVDCCPDLimits(
                            ev_max_charge_power=30000,
                            ev_min_charge_power=100,
                            ev_max_charge_current=300,
                            ev_min_charge_current=10,
                            ev_max_voltage=1000,
                            ev_min_voltage=10,
                            target_soc=80,
                            ev_max_discharge_power=11,
                            ev_min_discharge_power=1000,
                            ev_max_discharge_current=11,
                            ev_min_discharge_current=10,
                        )
                    ),
                ),
            ),
        ],
    )
    async def test_15118_20_dc_charge_parameter_discovery_res_ev_context_update(
        self, params, selected_service, expected_state, expected_ev_context
    ):
        self.comm_session.selected_energy_service = SelectedEnergyService(
            service=selected_service, is_free=True, parameter_set=None
        )
        dc_service_discovery = DCChargeParameterDiscovery(self.comm_session)
        dc_service_discovery_req = get_dc_service_discovery_req(
            params, selected_service
        )
        await dc_service_discovery.process_message(message=dc_service_discovery_req)
        assert dc_service_discovery.next_state is expected_state
        updated_ev_context = self.comm_session.evse_controller.ev_data_context
        assert updated_ev_context == expected_ev_context

    @pytest.mark.parametrize(
        "params, selected_service, control_mode, expected_state, expected_ev_context",
        [
            (
                ScheduledDCChargeLoopReqParams(
                    ev_target_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_energy_request=RationalNumber(exponent=2, value=300),
                    ev_target_current=RationalNumber(exponent=2, value=300),
                    ev_target_voltage=RationalNumber(exponent=2, value=300),
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=2, value=300),
                    ev_max_charge_current=RationalNumber(exponent=2, value=300),
                    ev_max_voltage=RationalNumber(exponent=2, value=300),
                    ev_min_voltage=RationalNumber(exponent=2, value=300),
                ),
                ServiceV20.DC,
                ControlMode.SCHEDULED,
                None,
                EVDataContext(
                    ev_session_context=EVSessionContext(
                        dc_limits=EVDCCLLimits(
                            ev_target_energy_request=30000,
                            ev_max_energy_request=30000,
                            ev_min_energy_request=30000,
                            ev_target_current=30000,
                            ev_target_voltage=30000,
                            ev_max_charge_power=30000,
                            ev_min_charge_power=30000,
                            ev_max_charge_current=30000,
                            ev_max_voltage=30000,
                            ev_min_voltage=30000,
                        )
                    ),
                ),
            ),
            (
                DynamicDCChargeLoopReqParams(
                    departure_time=3600,
                    ev_target_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=2, value=300),
                    ev_max_charge_current=RationalNumber(exponent=2, value=300),
                    ev_max_voltage=RationalNumber(exponent=2, value=300),
                    ev_min_voltage=RationalNumber(exponent=2, value=300),
                ),
                ServiceV20.DC,
                ControlMode.DYNAMIC,
                None,
                EVDataContext(
                    ev_session_context=EVSessionContext(
                        dc_limits=EVDCCLLimits(
                            departure_time=3600,
                            ev_target_energy_request=30000,
                            ev_max_energy_request=30000,
                            ev_min_energy_request=30000,
                            ev_max_charge_power=30000,
                            ev_min_charge_power=30000,
                            ev_max_charge_current=30000,
                            ev_max_voltage=30000,
                            ev_min_voltage=30000,
                        )
                    ),
                ),
            ),
            (
                BPTScheduledDCChargeLoopReqParams(
                    ev_target_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_energy_request=RationalNumber(exponent=2, value=300),
                    ev_target_current=RationalNumber(exponent=2, value=300),
                    ev_target_voltage=RationalNumber(exponent=2, value=300),
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=2, value=300),
                    ev_max_charge_current=RationalNumber(exponent=2, value=300),
                    ev_max_voltage=RationalNumber(exponent=2, value=300),
                    ev_min_voltage=RationalNumber(exponent=2, value=300),
                    ev_max_discharge_power=RationalNumber(exponent=2, value=300),
                    ev_min_discharge_power=RationalNumber(exponent=2, value=300),
                    ev_max_discharge_current=RationalNumber(exponent=2, value=300),
                ),
                ServiceV20.DC_BPT,
                ControlMode.SCHEDULED,
                None,
                EVDataContext(
                    ev_session_context=EVSessionContext(
                        dc_limits=EVDCCLLimits(
                            ev_target_energy_request=30000,
                            ev_max_energy_request=30000,
                            ev_min_energy_request=30000,
                            ev_target_current=30000,
                            ev_target_voltage=30000,
                            ev_max_charge_power=30000,
                            ev_min_charge_power=30000,
                            ev_max_charge_current=30000,
                            ev_max_voltage=30000,
                            ev_min_voltage=30000,
                            ev_max_discharge_power=30000,
                            ev_min_discharge_power=30000,
                            ev_max_discharge_current=30000,
                        )
                    ),
                ),
            ),
            (
                BPTDynamicDCChargeLoopReqParams(
                    departure_time=3600,
                    ev_target_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=2, value=300),
                    ev_max_charge_current=RationalNumber(exponent=2, value=300),
                    ev_max_voltage=RationalNumber(exponent=2, value=300),
                    ev_min_voltage=RationalNumber(exponent=2, value=300),
                    ev_max_discharge_power=RationalNumber(exponent=2, value=300),
                    ev_min_discharge_power=RationalNumber(exponent=2, value=300),
                    ev_max_discharge_current=RationalNumber(exponent=2, value=300),
                    ev_max_v2x_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_v2x_energy_request=RationalNumber(exponent=2, value=300),
                ),
                ServiceV20.DC_BPT,
                ControlMode.DYNAMIC,
                None,
                EVDataContext(
                    ev_session_context=EVSessionContext(
                        dc_limits=EVDCCLLimits(
                            departure_time=3600,
                            ev_target_energy_request=30000,
                            ev_max_energy_request=30000,
                            ev_min_energy_request=30000,
                            ev_max_charge_power=30000,
                            ev_min_charge_power=30000,
                            ev_max_charge_current=30000,
                            ev_max_voltage=30000,
                            ev_min_voltage=30000,
                            ev_max_discharge_power=30000,
                            ev_min_discharge_power=30000,
                            ev_max_discharge_current=30000,
                            ev_max_v2x_energy_request=30000,
                            ev_min_v2x_energy_request=30000,
                        )
                    ),
                ),
            ),
        ],
    )
    async def test_15118_20_dc_charge_charge_loop_res_ev_context_update(
        self,
        params,
        selected_service,
        control_mode,
        expected_state,
        expected_ev_context,
    ):
        self.comm_session.control_mode = control_mode
        self.comm_session.selected_energy_service = SelectedEnergyService(
            service=selected_service, is_free=True, parameter_set=None
        )
        dc_charge_loop = DCChargeLoop(self.comm_session)
        dc_charge_loop_req = get_dc_charge_loop_req(
            params, selected_service, control_mode
        )

        await dc_charge_loop.process_message(message=dc_charge_loop_req)
        assert dc_charge_loop.next_state is expected_state
        updated_ev_context = self.comm_session.evse_controller.ev_data_context
        assert (
            updated_ev_context.ev_session_context
            == expected_ev_context.ev_session_context
        )

    @pytest.mark.parametrize(
        "req_params, expected_res_params, selected_service, expected_state, expected_evse_context",  # noqa
        [
            (
                DCChargeParameterDiscoveryReqParams(
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=0, value=100),
                    ev_max_charge_current=RationalNumber(exponent=0, value=300),
                    ev_min_charge_current=RationalNumber(exponent=0, value=10),
                    ev_max_voltage=RationalNumber(exponent=0, value=1000),
                    ev_min_voltage=RationalNumber(exponent=0, value=10),
                    target_soc=80,
                ),
                DCChargeParameterDiscoveryResParams(
                    evse_max_charge_power=RationalNumber(exponent=0, value=30000),
                    evse_min_charge_power=RationalNumber(exponent=-2, value=10000),
                    evse_max_charge_current=RationalNumber(exponent=0, value=30000),
                    evse_min_charge_current=RationalNumber(exponent=-2, value=10000),
                    evse_max_voltage=RationalNumber(exponent=0, value=30000),
                    evse_min_voltage=RationalNumber(exponent=-2, value=10000),
                    evse_power_ramp_limit=RationalNumber(exponent=-2, value=10000),
                ),
                ServiceV20.DC,
                ScheduleExchange,
                EVSEDataContext(
                    rated_limits=EVSERatedLimits(
                        dc_limits=EVSEDCCPDLimits(
                            evse_max_charge_power=30000,
                            evse_min_charge_power=100,
                            evse_max_charge_current=30000,
                            evse_min_charge_current=100,
                            evse_max_voltage=30000,
                            evse_min_voltage=100,
                            evse_power_ramp_limit=100,
                        )
                    ),
                    session_context=None,
                ),
            ),
            (
                BPTDCChargeParameterDiscoveryReqParams(
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=0, value=100),
                    ev_max_charge_current=RationalNumber(exponent=0, value=300),
                    ev_min_charge_current=RationalNumber(exponent=0, value=10),
                    ev_max_voltage=RationalNumber(exponent=0, value=1000),
                    ev_min_voltage=RationalNumber(exponent=0, value=10),
                    target_soc=80,
                    ev_max_discharge_power=RationalNumber(exponent=0, value=11),
                    ev_min_discharge_power=RationalNumber(exponent=3, value=1),
                    ev_max_discharge_current=RationalNumber(exponent=0, value=11),
                    ev_min_discharge_current=RationalNumber(exponent=0, value=10),
                ),
                BPTDCChargeParameterDiscoveryResParams(
                    evse_max_charge_power=RationalNumber(exponent=0, value=30000),
                    evse_min_charge_power=RationalNumber(exponent=-2, value=10000),
                    evse_max_charge_current=RationalNumber(exponent=0, value=30000),
                    evse_min_charge_current=RationalNumber(exponent=-2, value=10000),
                    evse_max_voltage=RationalNumber(exponent=0, value=30000),
                    evse_min_voltage=RationalNumber(exponent=-2, value=10000),
                    evse_power_ramp_limit=RationalNumber(exponent=-2, value=10000),
                    evse_max_discharge_power=RationalNumber(exponent=0, value=30000),
                    evse_min_discharge_power=RationalNumber(exponent=-2, value=10000),
                    evse_max_discharge_current=RationalNumber(exponent=0, value=30000),
                    evse_min_discharge_current=RationalNumber(exponent=-2, value=10000),
                ),
                ServiceV20.DC_BPT,
                ScheduleExchange,
                EVSEDataContext(
                    rated_limits=EVSERatedLimits(
                        dc_limits=EVSEDCCPDLimits(
                            evse_max_charge_power=30000,
                            evse_min_charge_power=100,
                            evse_max_charge_current=30000,
                            evse_min_charge_current=100,
                            evse_max_voltage=30000,
                            evse_min_voltage=100,
                            evse_power_ramp_limit=100,
                        ),
                        dc_bpt_limits=EVSEDCBPTCPDLimits(
                            evse_max_discharge_power=30000,
                            evse_min_discharge_power=100,
                            evse_max_discharge_current=30000,
                            evse_min_discharge_current=100,
                        ),
                    ),
                ),
            ),
        ],
    )
    async def test_15118_20_dc_charge_param_discovery_res_evse_context_read(
        self,
        req_params,
        expected_res_params,
        selected_service,
        expected_state,
        expected_evse_context,
    ):
        self.comm_session.selected_energy_service = SelectedEnergyService(
            service=selected_service, is_free=True, parameter_set=None
        )
        self.comm_session.evse_controller.evse_data_context = expected_evse_context
        dc_service_discovery = DCChargeParameterDiscovery(self.comm_session)
        dc_service_discovery_req = get_dc_service_discovery_req(
            req_params, selected_service
        )
        await dc_service_discovery.process_message(message=dc_service_discovery_req)
        assert dc_service_discovery.next_state is expected_state
        assert isinstance(dc_service_discovery.message, DCChargeParameterDiscoveryRes)
        if selected_service == ServiceV20.DC:
            assert dc_service_discovery.message.dc_params == expected_res_params
        elif selected_service == ServiceV20.DC_BPT:
            assert dc_service_discovery.message.bpt_dc_params == expected_res_params

    @pytest.mark.parametrize(
        "ev_params,expected_evse_params,selected_service,control_mode,expected_state,evse_params,",  # noqa
        [
            (
                ScheduledDCChargeLoopReqParams(
                    ev_target_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_energy_request=RationalNumber(exponent=2, value=300),
                    ev_target_current=RationalNumber(exponent=2, value=300),
                    ev_target_voltage=RationalNumber(exponent=2, value=300),
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=2, value=300),
                    ev_max_charge_current=RationalNumber(exponent=2, value=300),
                    ev_max_voltage=RationalNumber(exponent=2, value=300),
                    ev_min_voltage=RationalNumber(exponent=2, value=300),
                ),
                ScheduledDCChargeLoopResParams(
                    evse_maximum_charge_power=RationalNumber(exponent=-2, value=30000),
                    evse_minimum_charge_power=RationalNumber(exponent=-1, value=6000),
                    evse_maximum_charge_current=RationalNumber(exponent=-1, value=7000),
                    evse_maximum_voltage=RationalNumber(exponent=-1, value=8000),
                ),
                ServiceV20.DC,
                ControlMode.SCHEDULED,
                None,
                EVSEDataContext(
                    rated_limits=EVSERatedLimits(
                        dc_limits=EVSEDCCPDLimits(
                            evse_max_charge_power=300,
                            evse_min_charge_power=600,
                            evse_max_charge_current=700,
                            evse_max_voltage=800,
                        )
                    ),
                    session_context=EVSESessionContext(
                        dc_limits=EVSEDCCLLimits(
                            evse_max_charge_power=300,
                            evse_min_charge_power=600,
                            evse_max_charge_current=700,
                            evse_max_voltage=800,
                        )
                    ),
                ),
            ),
            (
                DynamicDCChargeLoopReqParams(
                    departure_time=3600,
                    ev_target_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=2, value=300),
                    ev_max_charge_current=RationalNumber(exponent=2, value=300),
                    ev_max_voltage=RationalNumber(exponent=2, value=300),
                    ev_min_voltage=RationalNumber(exponent=2, value=300),
                ),
                DynamicDCChargeLoopRes(
                    departure_time=3600,
                    min_soc=30,
                    target_soc=80,
                    ack_max_delay=15,
                    evse_maximum_charge_power=RationalNumber(exponent=0, value=30000),
                    evse_minimum_charge_power=RationalNumber(exponent=-1, value=4000),
                    evse_maximum_charge_current=RationalNumber(exponent=-1, value=5000),
                    evse_maximum_voltage=RationalNumber(exponent=-1, value=6000),
                ),
                ServiceV20.DC,
                ControlMode.DYNAMIC,
                None,
                EVSEDataContext(
                    rated_limits=EVSERatedLimits(
                        dc_limits=EVSEDCCPDLimits(
                            evse_max_charge_power=30000,
                            evse_min_charge_power=400,
                            evse_max_charge_current=500,
                            evse_max_voltage=600,
                        )
                    ),
                    session_context=EVSESessionContext(
                        ev_departure_time=3600,
                        ev_min_soc=30,
                        ev_target_soc=80,
                        ack_max_delay=15,
                        dc_limits=EVSEDCCLLimits(
                            evse_max_charge_power=30000,
                            evse_min_charge_power=400,
                            evse_max_charge_current=500,
                            evse_max_voltage=600,
                        ),
                    ),
                ),
            ),
            (
                BPTScheduledDCChargeLoopReqParams(
                    ev_target_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_energy_request=RationalNumber(exponent=2, value=300),
                    ev_target_current=RationalNumber(exponent=2, value=300),
                    ev_target_voltage=RationalNumber(exponent=2, value=300),
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=2, value=300),
                    ev_max_charge_current=RationalNumber(exponent=2, value=300),
                    ev_max_voltage=RationalNumber(exponent=2, value=300),
                    ev_min_voltage=RationalNumber(exponent=2, value=300),
                    ev_max_discharge_power=RationalNumber(exponent=2, value=300),
                    ev_min_discharge_power=RationalNumber(exponent=2, value=300),
                    ev_max_discharge_current=RationalNumber(exponent=2, value=300),
                ),
                BPTScheduledDCChargeLoopResParams(
                    evse_maximum_charge_power=RationalNumber(exponent=-2, value=30000),
                    evse_minimum_charge_power=RationalNumber(exponent=-1, value=4000),
                    evse_maximum_charge_current=RationalNumber(exponent=-1, value=5000),
                    evse_maximum_voltage=RationalNumber(exponent=-1, value=6000),
                    evse_max_discharge_power=RationalNumber(exponent=-1, value=7000),
                    evse_min_discharge_power=RationalNumber(exponent=-1, value=8000),
                    evse_max_discharge_current=RationalNumber(exponent=-1, value=9000),
                    evse_min_voltage=RationalNumber(exponent=-2, value=10000),
                ),
                ServiceV20.DC_BPT,
                ControlMode.SCHEDULED,
                None,
                EVSEDataContext(
                    rated_limits=EVSERatedLimits(
                        dc_limits=EVSEDCCPDLimits(
                            evse_max_charge_power=300,
                            evse_min_charge_power=400,
                            evse_max_charge_current=500,
                            evse_max_voltage=600,
                            evse_min_voltage=100,
                        ),
                        dc_bpt_limits=EVSEDCBPTCPDLimits(
                            evse_max_discharge_power=700,
                            evse_min_discharge_power=800,
                            evse_max_discharge_current=900,
                        ),
                    ),
                    session_context=EVSESessionContext(
                        dc_limits=EVSEDCCLLimits(
                            evse_max_charge_power=300,
                            evse_min_charge_power=400,
                            evse_max_charge_current=500,
                            evse_max_voltage=600,
                            evse_max_discharge_power=700,
                            evse_min_discharge_power=800,
                            evse_max_discharge_current=900,
                            evse_min_voltage=100,
                        )
                    ),
                ),
            ),
            (
                BPTDynamicDCChargeLoopReqParams(
                    departure_time=3600,
                    ev_target_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_energy_request=RationalNumber(exponent=2, value=300),
                    ev_max_charge_power=RationalNumber(exponent=2, value=300),
                    ev_min_charge_power=RationalNumber(exponent=2, value=300),
                    ev_max_charge_current=RationalNumber(exponent=2, value=300),
                    ev_max_voltage=RationalNumber(exponent=2, value=300),
                    ev_min_voltage=RationalNumber(exponent=2, value=300),
                    ev_max_discharge_power=RationalNumber(exponent=2, value=300),
                    ev_min_discharge_power=RationalNumber(exponent=2, value=300),
                    ev_max_discharge_current=RationalNumber(exponent=2, value=300),
                    ev_max_v2x_energy_request=RationalNumber(exponent=2, value=300),
                    ev_min_v2x_energy_request=RationalNumber(exponent=2, value=300),
                ),
                BPTDynamicDCChargeLoopRes(
                    departure_time=3600,
                    min_soc=30,
                    target_soc=80,
                    ack_max_delay=15,
                    evse_maximum_charge_power=RationalNumber(exponent=0, value=10000),
                    evse_minimum_charge_power=RationalNumber(exponent=0, value=20000),
                    evse_maximum_charge_current=RationalNumber(exponent=0, value=30000),
                    evse_maximum_voltage=RationalNumber(exponent=0, value=4000),
                    evse_max_discharge_power=RationalNumber(exponent=0, value=5000),
                    evse_min_discharge_power=RationalNumber(exponent=0, value=6000),
                    evse_max_discharge_current=RationalNumber(exponent=0, value=7000),
                    evse_min_voltage=RationalNumber(exponent=0, value=8000),
                ),
                ServiceV20.DC_BPT,
                ControlMode.DYNAMIC,
                None,
                EVSEDataContext(
                    rated_limits=EVSERatedLimits(
                        dc_limits=EVSEDCCPDLimits(
                            evse_max_charge_power=10000,
                            evse_min_charge_power=20000,
                            evse_max_charge_current=30000,
                            evse_max_voltage=4000,
                            evse_min_voltage=8000,
                        ),
                        dc_bpt_limits=EVSEDCBPTCPDLimits(
                            evse_max_discharge_power=5000,
                            evse_min_discharge_power=6000,
                            evse_max_discharge_current=7000,
                        ),
                    ),
                    session_context=EVSESessionContext(
                        ev_departure_time=3600,
                        ev_min_soc=30,
                        ev_target_soc=80,
                        ack_max_delay=15,
                        dc_limits=EVSEDCCLLimits(
                            evse_max_charge_power=10000,
                            evse_min_charge_power=20000,
                            evse_max_charge_current=30000,
                            evse_max_voltage=4000,
                            evse_max_discharge_power=5000,
                            evse_min_discharge_power=6000,
                            evse_max_discharge_current=7000,
                            evse_min_voltage=8000,
                        ),
                    ),
                ),
            ),
        ],
    )
    async def test_15118_20_dc_charge_charge_loop_res_evse_context_read(
        self,
        ev_params,
        expected_evse_params,
        selected_service,
        control_mode,
        expected_state,
        evse_params,
    ):
        self.comm_session.control_mode = control_mode
        self.comm_session.selected_energy_service = SelectedEnergyService(
            service=selected_service, is_free=True, parameter_set=None
        )
        self.comm_session.evse_controller.evse_data_context = evse_params
        self.comm_session.evse_controller.send_charging_power_limits = AsyncMock(
            return_value=None
        )
        dc_charge_loop = DCChargeLoop(self.comm_session)
        dc_charge_loop_req = get_dc_charge_loop_req(
            ev_params, selected_service, control_mode
        )
        await dc_charge_loop.process_message(message=dc_charge_loop_req)
        assert dc_charge_loop.next_state is expected_state
        assert isinstance(dc_charge_loop.message, DCChargeLoopRes)
        if selected_service == ServiceV20.DC and control_mode == ControlMode.SCHEDULED:
            assert (
                dc_charge_loop.message.scheduled_dc_charge_loop_res
                == expected_evse_params
            )
        elif (
            selected_service == ServiceV20.DC_BPT
            and control_mode == ControlMode.SCHEDULED
        ):
            assert (
                dc_charge_loop.message.bpt_scheduled_dc_charge_loop_res
                == expected_evse_params
            )
        if selected_service == ServiceV20.DC and control_mode == ControlMode.DYNAMIC:
            assert (
                dc_charge_loop.message.dynamic_dc_charge_loop_res
                == expected_evse_params
            )
        elif (
            selected_service == ServiceV20.DC_BPT
            and control_mode == ControlMode.DYNAMIC
        ):
            assert (
                dc_charge_loop.message.bpt_dynamic_dc_charge_loop_res
                == expected_evse_params
            )

    @pytest.mark.parametrize(
        "control_mode, next_state, selected_energy_service, cp_state",
        [
            (
                ControlMode.DYNAMIC,
                DCChargeLoop,
                SelectedEnergyService(
                    service=ServiceV20.DC, is_free=True, parameter_set=None
                ),
                CpState.D2,
            ),
            (
                ControlMode.DYNAMIC,
                DCChargeLoop,
                SelectedEnergyService(
                    service=ServiceV20.DC, is_free=True, parameter_set=None
                ),
                CpState.C2,
            ),
            (
                ControlMode.DYNAMIC,
                Terminate,
                SelectedEnergyService(
                    service=ServiceV20.DC, is_free=True, parameter_set=None
                ),
                CpState.B2,
            ),
        ],
    )
    async def test_power_delivery_state_check(
        self, control_mode, next_state, selected_energy_service, cp_state
    ):
        self.comm_session.control_mode = control_mode
        self.comm_session.selected_energy_service = selected_energy_service
        power_delivery = PowerDelivery(self.comm_session)
        self.comm_session.evse_controller.get_cp_state = AsyncMock(
            return_value=cp_state
        )
        await power_delivery.process_message(
            message=get_power_delivery_req(Processing.FINISHED, ChargeProgress.START)
        )
        assert power_delivery.next_state is next_state

    @pytest.mark.parametrize(
        "float_value, expected_exponent, expected_value",
        [
            (-6340, 0, -6340),
            (-634, -1, -6340),
            (-234, -2, -23400),
            (-0.634, -3, -634),
            (-0.634, -3, -634),
            (-0.0634, -3, -63),
            (-0.00634, -3, -6),
            (-0.000634, -3, 0),
            (-0.0000634, -3, 0),
            (0.0, 0, 0),
            (0.0000234, -3, 0),
            (0.000234, -3, 0),
            (0.00234, -3, 2),
            (0.0234, -3, 23),
            (0.234, -3, 234),
            (2.34, -3, 2340),
            (23.4, -3, 23400),
            (234, -2, 23400),
            (2340, -1, 23400),
            (23400, 0, 23400),
            (234000, 1, 23400),
            (0.4, -3, 400),
            (400, -1, 4000),
            (32767, 0, 32767),
            (32768, 1, 3276),
        ],
    )
    async def test_exponent_conversion_for_rational_number_type(
        self,
        float_value: float,
        expected_exponent: int,
        expected_value: int,
    ):  # noqa: ANN201
        """Test conversion of a value into its exponent form.

        This test particularly tests the conversion suitable for
        the Rational Number type of ISO 15118-20, considering
        its value range [-32768, 32767].
        The byte range still considers the one from ISO 15118-2:
        [-3, 3]
        """
        exponent, value = RationalNumber._convert_to_exponent_number(float_value)

        assert exponent == expected_exponent
        assert value == expected_value
