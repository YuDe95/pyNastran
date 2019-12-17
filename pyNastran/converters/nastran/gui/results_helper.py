"""Interface for converting OP2 results to the GUI format"""
# pylint: disable=C1801, C0103
from __future__ import annotations
import os
from collections import defaultdict
from typing import Tuple, Dict, Union, Any, TYPE_CHECKING

import numpy as np
from numpy.linalg import norm  # type: ignore

from pyNastran.gui.gui_objects.gui_result import GuiResult, GuiResultIDs
from pyNastran.converters.nastran.gui.geometry_helper import NastranGuiAttributes
from pyNastran.gui.gui_objects.displacements import (
    DisplacementResults, ForceTableResults) #, TransientElementResults
from pyNastran.op2.result_objects.stress_object import (
    _get_nastran_header,
    get_rod_stress_strain,
    get_bar_stress_strain, get_bar100_stress_strain, get_beam_stress_strain,
    get_plate_stress_strain, get_solid_stress_strain
)
from pyNastran.gui.gui_objects.gui_result import GridPointForceResult
from pyNastran.converters.nastran.gui.results import SimpleTableResults, LayeredTableResults

if TYPE_CHECKING:
    from pyNastran.op2.op2 import OP2
    from pyNastran.gui.gui_objects.settings import Settings
    #from pyNastran.op2.result_objects.design_response import Desvars

GuiResults = Union[GuiResult, GuiResultIDs, GridPointForceResult]

class NastranGuiResults(NastranGuiAttributes):
    """Defines OP2 specific methods NastranIO"""
    def __init__(self):
        super(NastranGuiResults, self).__init__()

    def _fill_grid_point_forces(self, cases, model, key, icase,
                                form_dict, header_dict, keys_map):
        if key not in model.grid_point_forces:
            return icase
        grid_point_forces = model.grid_point_forces[key]
        case = grid_point_forces
        if not case.is_real:
            #raise RuntimeError(grid_point_forces.is_real)
            return icase

        subcase_id = key[0]
        title = 'Grid Point Forces'
        header = 'Grid Point Forces'
        nastran_res = GridPointForceResult(subcase_id, header, title, grid_point_forces)

        itime = 0
        print('GridPointForceResult', icase)
        cases[icase] = (nastran_res, (itime, 'Grid Point Forces'))
        formii = ('Grid Point Forces', icase, [])
        form_dict[(key, itime)].append(formii)

        dt = case._times[itime]
        header = _get_nastran_header(case, dt, itime)
        header_dict[(key, itime)] = header
        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)

        icase += 1
        return icase

    def _fill_op2_oug_oqg(self, cases, model: OP2, key, icase: int,
                          form_dict, header_dict, keys_map, log) -> int:
        """
        loads nodal results bector results (e.g., dispalcements/temperatures)
        """
        nnodes = self.nnodes
        node_ids = self.node_ids
        icase = _fill_nastran_displacements(
            cases, model, key, icase,
            form_dict, header_dict, keys_map,
            self.xyz_cid0,
            nnodes, node_ids, log, dim_max=self.gui.settings.dim_max)

        icase = _fill_nastran_temperatures(
            cases, model, key, icase,
            form_dict, header_dict, keys_map,
            nnodes, log)
        return icase

    def _fill_op2_gpstress(self, cases, model: OP2,
                                times, key, icase: int,
                                form_dict, header_dict, keys_map) -> int:
        """Creates the time accurate grid point stress objects"""
        if key in model.grid_point_stress_discontinuities:
            case = model.grid_point_stress_discontinuities[key]
            self.log.warning('skipping grid_point_stress_discontinuities')
        if key in model.grid_point_stresses_volume_principal:
            case = model.grid_point_stresses_volume_principal[key]
            self.log.warning('skipping grid_point_stresses_volume_principal')

        icase = self._fill_op2_grid_point_surface_stresses(
            cases, model,
            times, key, icase,
            form_dict, header_dict, keys_map)

        icase = self._fill_op2_grid_point_stresses_volume_direct(
            cases, model,
            times, key, icase,
            form_dict, header_dict, keys_map)
        return icase

    def _fill_op2_grid_point_stresses_volume_direct(self, cases, model: OP2,
                                                    times, key, icase: int,
                                                    form_dict, header_dict, keys_map) -> int:
        if key not in model.grid_point_stresses_volume_direct:
            return icase

        case = model.grid_point_stresses_volume_direct[key]
        if case.is_complex:
            return icase
        nids = self.node_ids
        nnodes = len(nids)

        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)
        subcase_id = key[0]

        nids2 = case.node
        i = np.searchsorted(nids, nids2)
        if len(i) != len(np.unique(i)):
            msg = 'i_gpstress=%s is not unique\n' % str(i)
            #print('eids = %s\n' % str(list(eids)))
            #print('eidsi = %s\n' % str(list(eidsi)))
            raise RuntimeError(msg)

        for itime, unused_dt in enumerate(times):
            dt = case._times[itime]
            header = _get_nastran_header(case, dt, itime)
            header_dict[(key, itime)] = header

            # volume direct
            #['ox', 'oy', 'oz', 'txy', 'tyz', 'txz', 'pressure', 'ovm']
            ox = np.full(nnodes, np.nan, dtype='float32')
            oy = np.full(nnodes, np.nan, dtype='float32')
            oz = np.full(nnodes, np.nan, dtype='float32')
            txy = np.full(nnodes, np.nan, dtype='float32')
            tyz = np.full(nnodes, np.nan, dtype='float32')
            txz = np.full(nnodes, np.nan, dtype='float32')
            ovm = np.full(nnodes, np.nan, dtype='float32')

            ox[i] = case.data[itime, :, 0]
            oy[i] = case.data[itime, :, 1]
            oz[i] = case.data[itime, :, 2]
            txy[i] = case.data[itime, :, 3]
            tyz[i] = case.data[itime, :, 4]
            txz[i] = case.data[itime, :, 5]
            ovm[i] = case.data[itime, :, 7]

            headers = ['oxx', 'oyy', 'ozz', 'txy', 'tyz', 'txz', 'ovm']
            form = [('Volume Direct', None, [])]
            formi = form[0][2]
            form_dict[(key, itime)] = form

            for header, resi in zip(headers, (ox, oy, oz, txy, tyz, txz, ovm)):
                ese_res = GuiResult(subcase_id, header=header,
                                    title=header, data_format='%.3e',
                                    location='node', scalar=resi)
                cases[icase] = (ese_res, (subcase_id, header))
                formi.append((header, icase, []))
                icase += 1

        return icase

    def _fill_op2_grid_point_surface_stresses(self, cases, model: OP2,
                                              times, key, icase: int,
                                              form_dict, header_dict, keys_map) -> int:
        if key not in model.grid_point_surface_stresses:
            return icase

        #grid_point_surface_stresses[(1, 1, 1, 0, 666, '', '')]
        #    type=GridPointSurfaceStressesArray nelements=99
        #    data: [1, nelements, 8] where 8=[nx, ny, txy, angle, majorP, minorP, tmax, ovm]
        #    node_element.shape = (99, 2)
        #    location.shape = (99,)
        #    data.shape = (1, 99, 8)
        #    sort1
        #    lsdvmns = [1]
        case = model.grid_point_surface_stresses[key]

        if case.is_complex:
            return icase
        #print(case.get_stats())
        eids_all = self.element_ids
        nelements = len(eids_all)
        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)
        subcase_id = key[0]


        eidsi = case.node_element[:, 0]
        nidsi = case.node_element[:, 1]

        icentroid = np.where(nidsi == 0)[0]
        eids_res = eidsi[icentroid]
        assert eids_res.min() > 0, eids_res
        ueids_res = np.unique(eids_res)
        #print('eids_res =', eids_res.tolist(), len(eids_res))
        #print('ueids_res=', ueids_res.tolist(), len(ueids_res))

        i = np.searchsorted(eids_all, ueids_res)
        ui = np.unique(i)
        j = np.where(i < len(ui) - 1)[0]
        i2 = i[j]

        #print('i        =', i.tolist(), len(i))
        #print('ui       =', ui.tolist(), len(ui))
        #print('j        =', j.tolist(), len(j))
        #print('i2       =', i2.tolist(), len(i2))
        #ueids_res2 = eids_all[i2]

        #ueids_res1 = ueids_res[:len(ui) - 1]
        #print('ueids_res1 =', ueids_res1.tolist(), len(ueids_res1))
        #print('ueids_res2 =', ueids_res2.tolist(), len(ueids_res2))

        #eid_exists = ueids_res1 == ueids_res2
        #print("eid_exists =", eid_exists)
        #ueids3 = ueids_res1[eid_exists]
        #print('ueids3=', ueids3, len(ueids3))

        if len(i2) != len(np.unique(i2)):
            msg = 'i_gpstress=%s is not unique\n' % str(i2)
            #print('eids = %s\n' % str(list(eids)))
            #print('eidsi = %s\n' % str(list(eidsi)))
            raise RuntimeError(msg)

        for itime, unused_dt in enumerate(times):
            dt = case._times[itime]
            header = _get_nastran_header(case, dt, itime)
            header_dict[(key, itime)] = header

            # [nx, ny, txy, angle, majorP, minorP, tmax, ovm]
            nx = np.full(nelements, np.nan, dtype='float32')
            ny = np.full(nelements, np.nan, dtype='float32')
            txy = np.full(nelements, np.nan, dtype='float32')
            angle = np.full(nelements, np.nan, dtype='float32')
            major = np.full(nelements, np.nan, dtype='float32')
            minor = np.full(nelements, np.nan, dtype='float32')
            tmax = np.full(nelements, np.nan, dtype='float32')
            ovm = np.full(nelements, np.nan, dtype='float32')

            nx[i2] = case.data[itime, i2, 0]
            ny[i2] = case.data[itime, i2, 1]
            txy[i2] = case.data[itime, i2, 2]
            angle[i2] = case.data[itime, i2, 3]
            major[i2] = case.data[itime, i2, 4]
            minor[i2] = case.data[itime, i2, 5]
            tmax[i2] = case.data[itime, i2, 6]
            ovm[i2] = case.data[itime, i2, 7]

            headers = ['nx', 'ny', 'txy', 'majorP', 'minorP', 'tmax', 'ovm']
            form = [('Surface Stresses', None, [])]
            formi = form[0][2]
            form_dict[(key, itime)] = form

            for header, resi in zip(headers, (nx, ny, txy, angle, major, minor, ovm)):
                ese_res = GuiResult(subcase_id, header=header,
                                    title=header, data_format='%.3e',
                                    location='centroid', scalar=resi)
                cases[icase] = (ese_res, (subcase_id, header))
                formi.append((header, icase, []))
                icase += 1
        return icase

    def _fill_op2_centroidal_strain_energy(self, cases: Dict[int, GuiResults], model: OP2,
                                                times, key, icase: int,
                                                form_dict, header_dict, keys_map) -> int:
        """Creates the time accurate strain energy objects"""
        case = None

        # (isubcase, analysis_code, sort_method,
        #  count, ogs, superelement_adaptivity_index, pval_step) = key ????
        subcase_id = key[0]

        strain_energy = model.op2_results.strain_energy
        strain_energies = [
            # results_dict, name, flag of the element being supported
            (strain_energy.cquad4_strain_energy, 'CQUAD4', True),
            (strain_energy.cquad8_strain_energy, 'CQUAD8', True),
            (strain_energy.cquadr_strain_energy, 'CQUADR', True),
            (strain_energy.cquadx_strain_energy, 'CQUADX', True),

            (strain_energy.ctria3_strain_energy, 'CTRIA3', True),
            (strain_energy.ctria6_strain_energy, 'CTRIA6', True),
            (strain_energy.ctriar_strain_energy, 'CTRIAR', True),
            (strain_energy.ctriax_strain_energy, 'CTRIAX', True),
            (strain_energy.ctriax6_strain_energy, 'CTRIAX6', True),

            (strain_energy.ctetra_strain_energy, 'CTETRA', True),
            (strain_energy.cpenta_strain_energy, 'CPENTA', True),
            (strain_energy.chexa_strain_energy, 'CHEXA', True),
            (strain_energy.cpyram_strain_energy, 'CPYRAM', True),

            (strain_energy.crod_strain_energy, 'CROD', True),
            (strain_energy.ctube_strain_energy, 'CTUBE', True),
            (strain_energy.conrod_strain_energy, 'CONROD', True),

            (strain_energy.cbar_strain_energy, 'CBAR', True),
            (strain_energy.cbeam_strain_energy, 'CBEAM', True),

            (strain_energy.cgap_strain_energy, 'CGAP', True),
            (strain_energy.celas1_strain_energy, 'CELAS1', True),
            (strain_energy.celas2_strain_energy, 'CELAS2', True),
            (strain_energy.celas3_strain_energy, 'CELAS3', True),
            (strain_energy.celas4_strain_energy, 'CELAS4', True),
            (strain_energy.cdum8_strain_energy, 'CDUM8', False),
            (strain_energy.cbush_strain_energy, 'CBUSH', True),
            #(strain_energy.chexa8fd_strain_energy, '', False),
            (strain_energy.cbend_strain_energy, 'CBEND', False),
            (strain_energy.dmig_strain_energy, 'DMIG', False),
            (strain_energy.genel_strain_energy, 'GENEL', False),
            (strain_energy.cshear_strain_energy, 'CSHEAR', True),
            (strain_energy.conm2_strain_energy, 'CONM2', False),
        ]
        #  find the cases that have results for this key
        has_strain_energy = [key in res[0] for res in strain_energies]
        if not any(has_strain_energy):
            return icase
        itrue = has_strain_energy.index(True)
        unused_ese0 = strain_energies[itrue][0]
        #times = ese0._times

        #fmt = '%g'
        #header = ''
        #form0 = ('Element Strain Energy', None, [])

        #op2.strain_energy[1]
            #type=StrainEnergyObject ntimes=3 nelements=16
            #energy, percent, density
            #modes = [1, 2, 3]

        nelements = self.nelements
        eids = self.element_ids

        for itime, unused_dt in enumerate(times):
            ese = np.full(nelements, np.nan, dtype='float32')
            percent = np.full(nelements, np.nan, dtype='float32')
            strain_energy_density = np.full(nelements, np.nan, dtype='float32')
            for istrain_energy, is_true in enumerate(has_strain_energy):
                if not is_true:
                    continue
                resdict, name, unused_flag = strain_energies[istrain_energy]
                case = resdict[key]

                dt = case._times[itime]
                header = _get_nastran_header(case, dt, itime)
                header_dict[(key, itime)] = header
                keys_map[key] = (case.subtitle, case.label,
                                 case.superelement_adaptivity_index, case.pval_step)

                if case.is_complex:
                    continue

                data = case.data
                itotals = np.where(case.element[itime, :] == 100000000)[0]
                assert len(itotals) == 1, itotals
                itotal = itotals[0]

                eidsi2 = case.element[itime, :itotal]

                # find eids2i in eids
                i = np.searchsorted(eids, eidsi2)
                #if 0 and name == 'CELAS1':  # pragma: no cover
                    ## check that the elements were mapped correctly
                    #eids_actual = self.element_ids[i]
                    #for eid in eids_actual:
                        #element = self.model.elements[eid]
                        #assert element.type == name, element
                    #assert np.all(eids_actual == eidsi2)

                if len(i) != len(np.unique(i)):
                    msg = 'Strain Energy i%s=%s is not unique because there are missing elements' % (name, str(i))
                    model.log.warning(msg)
                    continue

                # verifies the try-except is what we think it is (missing elements)
                esei = data[itime, :itotal, 0]

                try:
                    ese[i] = esei
                    percent[i] = data[itime, :itotal, 1]
                    strain_energy_density[i] = data[itime, :itotal, 2]
                except IndexError:
                    model.log.warning('error reading Strain Energy')
                    continue

            # helicopter.dat
            #CBEAM : 10
            #CQUAD4 : 11388
            #CROD : 544
            #CTRIA3 : 151
            # nelements = 12093

            if np.any(np.isfinite(ese)):
                ese_res = GuiResult(subcase_id, header='Strain Energy: ' + header,
                                    title='Strain Energy', data_format='%.3e',
                                    location='centroid', scalar=ese)
                percent_res = GuiResult(subcase_id, header='Percent of Total: '+ header,
                                        title='Percent of Total', data_format='%.3f',
                                        location='centroid', scalar=percent)
                cases[icase] = (ese_res, (subcase_id, 'Strain Energy'))
                cases[icase + 1] = (percent_res, (subcase_id, 'Percent'))

                form_dict[(key, itime)].append(('Strain Energy', icase, []))
                form_dict[(key, itime)].append(('Percent', icase + 1, []))
                icase += 2
                if np.any(np.isfinite(strain_energy_density)):
                    sed_res = GuiResult(subcase_id, header='Strain Energy Density: ' + header,
                                        title='Strain Energy Density', data_format='%.3e',
                                        location='centroid', scalar=strain_energy_density)
                    cases[icase] = (sed_res, (subcase_id, 'Strain Energy Density'))
                    form_dict[(key, itime)].append(('Strain Energy Density', icase, []))
                    icase += 1
        return icase

    def _create_op2_time_centroidal_force_arrays(self, model, nelements, key, itime,
                                                 header_dict, keys_map):
        """
        creates the following force outputs:
         - fx, fy, fz, mx, my, mz
         - thermal_load
        """
        element_ids = self.element_ids
        fx = np.zeros(nelements, dtype='float32') # axial
        fy = np.zeros(nelements, dtype='float32') # shear_y
        fz = np.zeros(nelements, dtype='float32') # shear_z

        rx = np.zeros(nelements, dtype='float32') # torque
        ry = np.zeros(nelements, dtype='float32') # bending_y
        rz = np.zeros(nelements, dtype='float32') # bending_z

        is_element_on = np.zeros(nelements, dtype='float32') # torque
        unused_fmt = '%g'
        header = ''
        unused_form0 = ('Force', None, [])

        case = None
        found_force = False
        for res_type in (model.conrod_force, model.crod_force, model.ctube_force):
            if key in res_type:
                found_force = True
                case = res_type[key]
                if case.is_complex:
                    continue
                keys_map[key] = (case.subtitle, case.label,
                                 case.superelement_adaptivity_index, case.pval_step)
                data = case.data
                if case.nonlinear_factor is None:
                    unused_ntimes = data.shape[:1]
                    eids = case.element
                    dt = case._times[itime]
                    header = _get_nastran_header(case, dt, itime)
                    header_dict[(key, itime)] = header
                    #eids_to_find = intersect1d(self.element_ids, eids)
                    i = np.searchsorted(element_ids, eids)
                    assert np.array_equal(element_ids[i], eids)
                    fxi = data[itime, :, 0]
                    rxi = data[itime, :, 1]
                    if fxi.size != i.size:
                        msg = 'fx.size=%s i.size=%s fx=%s eids_to_find=%s' % (
                            fxi.size, i.size, fxi, eids)
                        raise RuntimeError(msg)
                    fx[i] = fxi
                    rx[i] = rxi
                    is_element_on[i] = 1.
                else:
                    continue

        if key in model.cbar_force:
            found_force = True
            ## CBAR-34
            case = model.cbar_force[key]
            if case.is_real:
                eids = case.element
                i = np.searchsorted(element_ids, eids)
                is_element_on[i] = 1.

                dt = case._times[itime]
                header = _get_nastran_header(case, dt, itime)
                header_dict[(key, itime)] = header
                keys_map[key] = (case.subtitle, case.label,
                                 case.superelement_adaptivity_index, case.pval_step)

                #[bending_moment_a1, bending_moment_a2, bending_moment_b1, bending_moment_b2,
                # shear1, shear2, axial, torque]
                #fx[i] = case.data[:, :, 6]
                #fy[i] = case.data[:, :, 4]
                #fz[i] = case.data[:, :, 5]

                if i.size == 1:
                    rxi = case.data[itime, :, 7].max()
                    ryi = np.vstack([case.data[itime, :, 0], case.data[itime, :, 2]]).max()
                    rzi = np.vstack([case.data[itime, :, 1], case.data[itime, :, 3]]).max()
                else:
                    rxi = case.data[itime, :, 7]#.max(axis=0)
                    ryi = np.vstack([case.data[itime, :, 0], case.data[itime, :, 2]]).max(axis=0)
                    rzi = np.vstack([case.data[itime, :, 1], case.data[itime, :, 3]]).max(axis=0)
                    unused_rzv = rzi

                    # rza = array([case.data[itime, :, 1], case.data[itime, :, 3]])#.max(axis=0)
                    # rzh = hstack([case.data[itime, :, 1], case.data[itime, :, 3]])#.max(axis=0)
                    # print(rzv.shape, rzv.shape, rzv.shape)
                assert rxi.size == i.size, 'rx.size=%s i.size=%s rx=%s' % (rxi.size, i.size, rxi)
                assert ryi.size == i.size, 'ry.size=%s i.size=%s ry=%s' % (ryi.size, i.size, ryi)
                assert rzi.size == i.size, 'rz.size=%s i.size=%s rz=%s' % (rzi.size, i.size, rzi)

                rx[i] = rxi
                ry[i] = ryi
                rz[i] = rzi

        if key in model.cbar_force_10nodes:
            found_force = True
            ## CBAR-100
            case = model.cbar_force_10nodes[key]
            eids = case.element
            ueids = np.unique(eids)

            dt = case._times[itime]
            header = _get_nastran_header(case, dt, itime)
            header_dict[(key, itime)] = header
            keys_map[key] = (case.subtitle, case.label,
                             case.superelement_adaptivity_index, case.pval_step)

            j = np.searchsorted(self.element_ids, ueids)
            di = j[1:-1] - j[0:-2]
            if len(di) == 0:
                # pload1
                self.log_error('Error loading CBAR-100 forces; failed slicing element_ids')
            else:
                is_element_on[j] = 1.

                if di.max() != 2:
                    #print('di =', np.unique(di))
                    # [station, bending_moment1, bending_moment2, shear1, shear2, axial, torque]
                    ii = 0
                    unused_eid_old = eids[0]
                    fxi = defaultdict(list)
                    fyi = defaultdict(list)
                    fzi = defaultdict(list)
                    rxi = defaultdict(list)
                    ryi = defaultdict(list)
                    rzi = defaultdict(list)
                    for ii, eid in enumerate(eids):
                        fxi[eid].append(case.data[:, ii, 5])
                        fyi[eid].append(case.data[:, ii, 3])
                        fzi[eid].append(case.data[:, ii, 4])

                        rxi[eid].append(case.data[:, ii, 6])
                        ryi[eid].append(case.data[:, ii, 1])
                        rzi[eid].append(case.data[:, ii, 2])
                        #if eidi == eid_old:
                        #    fx[ii] = array([case.data[:, j, 5], case.data[:, j, 5]]).max(axis=0)
                        #else:
                    for ii, eidi in zip(j, eids[j]):
                        fx[ii] = max(fxi[eidi])
                        fy[ii] = max(fyi[eidi])
                        fz[ii] = max(fyi[eidi])
                        rx[ii] = max(rxi[eidi])
                        ry[ii] = max(ryi[eidi])
                        rz[ii] = max(rzi[eidi])
                else:
                    # [station, bending_moment1, bending_moment2, shear1, shear2, axial, torque]
                    neids = len(np.unique(eids)) * 2
                    if len(eids) != len(np.unique(eids)) * 2:
                        msg = 'CBAR-100 Error: len(eids)=%s neids=%s' % (len(eids), neids)
                        raise RuntimeError(msg)
                    fx[i] = np.array(
                        [case.data[itime, ::-1, 5],
                         case.data[itime, 1::-1, 5]]).max(axis=0)
                    fy[i] = np.array(
                        [case.data[itime, ::-1, 3],
                         case.data[itime, 1::-1, 3]]).max(axis=0)
                    fz[i] = np.array(
                        [case.data[itime, ::-1, 4],
                         case.data[itime, 1::-1, 4]]).max(axis=0)
                    rx[i] = np.array(
                        [case.data[itime, ::-1, 6],
                         case.data[itime, 1::-1, 6]]).max(axis=0)
                    ry[i] = np.array(
                        [case.data[itime, ::-1, 1],
                         case.data[itime, 1::-1, 1]]).max(axis=0)
                    rz[i] = np.array(
                        [case.data[itime, ::-1, 2],
                         case.data[itime, 1::-1, 2]]).max(axis=0)
        return found_force, fx, fy, fz, rx, ry, rz, is_element_on

    def _fill_op2_time_centroidal_force(self, cases, model: OP2,
                                        key: Tuple[Any, int], icase: int, itime: int,
                                        form_dict: Dict[Any, Any],
                                        #form_dict: Dict[Tuple[Any, int], Any],
                                        header_dict: Dict[Any, Any],
                                        keys_map: Dict[Any, Any]) -> int:
        """
        Creates the time accurate strain energy objects for the pyNastranGUI
        """
        nelements = self.nelements
        out = self._create_op2_time_centroidal_force_arrays(
            model, nelements, key, itime, header_dict, keys_map)
        found_force, fx, fy, fz, rx, ry, rz, is_element_on = out

        #new_cases = True
        subcase_id = key[2]
        if found_force:
            fmt = '%.4f'
            # header = _get_nastran_header(case, dt, itime)

            #num_on = nelements
            num_off = 0
            if itime == 0 and is_element_on.min() == 0.0:
                ioff = np.where(is_element_on == 0)[0]
                num_off = len(ioff)
                print('force_eids_off = %s; n=%s' % (self.element_ids[ioff], num_off))
                self.log_error('force_eids_off = %s; n=%s' % (self.element_ids[ioff], num_off))
                force_on_res = GuiResult(subcase_id, header='Force - IsElementOn',
                                         title='Force\nIsElementOn',
                                         location='centroid', scalar=is_element_on)
                cases[icase] = (force_on_res, (subcase_id, 'Force\nIsElementOn'))
                form_dict[(key, itime)].append(('Force - IsElementOn', icase, []))
                #num_on -= num_off
                icase += 1

            if fx.min() != fx.max() or rx.min() != rx.max() and not num_off == nelements:
                # header = _get_nastran_header(case, dt, itime)
                header = header_dict[(key, itime)]
                fx_res = GuiResult(subcase_id, header=f'Axial: {header}', title='Axial',
                                   location='centroid', scalar=fx)
                fy_res = GuiResult(subcase_id, header=f'ShearY: {header}', title='ShearY',
                                   location='centroid', scalar=fy)
                fz_res = GuiResult(subcase_id, header=f'ShearZ: {header}', title='ShearZ',
                                   location='centroid', scalar=fz)
                mx_res = GuiResult(subcase_id, header=f'Torsion: {header}', title='Torsion',
                                   location='centroid', scalar=rx)
                my_res = GuiResult(subcase_id, header=f'BendingY: {header}', title='BendingY',
                                   location='centroid', scalar=ry)
                mz_res = GuiResult(subcase_id, header=f'BendingZ: {header}', title='BendingZ',
                                   location='centroid', scalar=rz)
                cases[icase] = (fx_res, (subcase_id, 'Axial'))
                cases[icase + 1] = (fy_res, (subcase_id, 'ShearY'))
                cases[icase + 2] = (fz_res, (subcase_id, 'ShearZ'))
                cases[icase + 3] = (mx_res, (subcase_id, 'Torsion'))
                cases[icase + 4] = (my_res, (subcase_id, 'BendingY'))
                cases[icase + 5] = (mz_res, (subcase_id, 'BendingZ'))

                form_dict[(key, itime)].append(('Axial', icase, []))
                form_dict[(key, itime)].append(('ShearY', icase + 1, []))
                form_dict[(key, itime)].append(('ShearZ', icase + 2, []))
                form_dict[(key, itime)].append(('Torque', icase + 3, []))
                form_dict[(key, itime)].append(('BendingY', icase + 4, []))
                form_dict[(key, itime)].append(('BendingZ', icase + 5, []))
                icase += 6

                is_axial = np.zeros(nelements, dtype='int8')
                is_shear_y = np.zeros(nelements, dtype='int8')
                is_shear_z = np.zeros(nelements, dtype='int8')
                is_torsion = np.zeros(nelements, dtype='int8')
                is_bending_y = np.zeros(nelements, dtype='int8')
                is_bending_z = np.zeros(nelements, dtype='int8')
                is_axial[np.where(np.abs(fx) > 0.0)[0]] = 1
                is_shear_y[np.where(np.abs(fy) > 0.0)[0]] = 1
                is_shear_z[np.where(np.abs(fz) > 0.0)[0]] = 1
                is_torsion[np.where(np.abs(rx) > 0.0)[0]] = 1
                is_bending_y[np.where(np.abs(ry) > 0.0)[0]] = 1
                is_bending_z[np.where(np.abs(rz) > 0.0)[0]] = 1
                #is_bending[where(abs(rx) > 0.0)[0]] = 1

                is_fx_res = GuiResult(subcase_id, header='IsAxial', title='IsAxial',
                                      location='centroid', scalar=is_axial, data_format=fmt)
                is_fy_res = GuiResult(subcase_id, header='IsShearY', title='IsShearY',
                                      location='centroid', scalar=is_shear_y, data_format=fmt)
                is_fz_res = GuiResult(subcase_id, header='IsShearZ', title='IsShearZ',
                                      location='centroid', scalar=is_shear_z, data_format=fmt)
                is_mx_res = GuiResult(subcase_id, header='IsTorsion', title='IsTorsion',
                                      location='centroid', scalar=is_torsion, data_format=fmt)
                is_my_res = GuiResult(subcase_id, header='IsBendingY', title='IsBendingY',
                                      location='centroid', scalar=is_bending_y, data_format=fmt)
                is_mz_res = GuiResult(subcase_id, header='IsBendingZ', title='IsBendingZ',
                                      location='centroid', scalar=is_bending_z, data_format=fmt)

                cases[icase] = (is_fx_res, (subcase_id, 'IsAxial'))
                cases[icase + 1] = (is_fy_res, (subcase_id, 'IsShearY'))
                cases[icase + 2] = (is_fz_res, (subcase_id, 'IsShearZ'))
                cases[icase + 3] = (is_mx_res, (subcase_id, 'IsTorsion'))
                cases[icase + 4] = (is_my_res, (subcase_id, 'IsBendingY'))
                cases[icase + 5] = (is_mz_res, (subcase_id, 'IsBendingZ'))

                form_dict[(key, itime)].append(('IsAxial', icase, []))
                form_dict[(key, itime)].append(('IsShearY', icase + 1, []))
                form_dict[(key, itime)].append(('IsShearZ', icase + 2, []))
                form_dict[(key, itime)].append(('IsTorsion', icase + 3, []))
                form_dict[(key, itime)].append(('IsBendingY', icase + 4, []))
                form_dict[(key, itime)].append(('IsBendingZ', icase + 5, []))
                icase += 6
        return icase

    def _fill_op2_time_centroidal_composite_stress(self, cases, model, key, icase: int, itime: int,
                                                   form_dict: Dict[Any, Any],
                                                   header_dict: Dict[Any, Any],
                                                   keys_map: Dict[Any, Any],
                                                   is_stress: int=True) -> int:
        nelements = self.nelements
        #oxx = np.full(nelements, np.nan, dtype='float32')
        #oyy = np.full(nelements, np.nan, dtype='float32')

        #txy = np.full(nelements, np.nan, dtype='float32')
        #tyz = np.full(nelements, np.nan, dtype='float32')
        #txz = np.full(nelements, np.nan, dtype='float32')

        #max_principal = np.full(nelements, np.nan, dtype='float32')  # max
        #min_principal = np.full(nelements, np.nan, dtype='float32')  # min
        #ovm = np.full(nelements, np.nan, dtype='float32')

        if is_stress:
            stress_obj = self.stress[key]
            word = 'Stress'
            fmt = '%.3f'
        else:
            stress_obj = self.strain[key]
            word = 'Strain'
            fmt = '%.4e'

        vm_word = None
        if len(stress_obj.composite_data_dict):
            print(stress_obj)
            out = stress_obj.set_composite_stress_by_layer(
                key, itime, nelements, header_dict,
            )
            vm_word, element_ids, oxx, oyy, txy, tyz, txz, max_principal, min_principal, ovm = out
        if vm_word is None:
            return icase

        #form0 = (word, None, [])
        #unused_formis = form0[2]
        subcase_id = key[2]
        if np.any(np.isfinite(oxx)):
            header = header_dict[(key, itime)]
            oxx_res = GuiResultIDs(subcase_id, header=word + f'XX: {header}', title=word + 'XX',
                                   location='centroid',
                                   ids=element_ids, scalar=oxx, data_format=fmt)
            cases[icase] = (oxx_res, (subcase_id, word + 'XX'))
            form_dict[(key, itime)].append((word + 'XX', icase, []))
            icase += 1
        return icase

    def _fill_op2_centroidal_stress(self, cases, model, times, key, icase_old,
                                    form_dict, header_dict, keys_map) -> int:
        """Creates the time accurate stress objects"""
        icase = icase_old
        settings = self.settings
        if settings.nastran_stress:
            for itime, unused_dt in enumerate(times):
                # shell stress
                try:
                    icase = self._fill_op2_time_centroidal_stress(
                        cases, model, key, icase_old, itime, form_dict, header_dict, keys_map,
                        is_stress=True)
                except IndexError:
                    self.log.error('problem getting stress...')
                    break
            if icase == icase_old:
                return icase

        #self.settings.nastran_plate_stress
        eids = self.element_ids
        if settings.nastran_plate_stress:
            icase = get_plate_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=True)
            icase = get_plate_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=True,
                prefix='modal_contribution',
            )

        if settings.nastran_composite_plate_stress:
            icase = get_composite_plate_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map,
                self.stress[key].composite_data_dict, is_stress=True)

        if settings.nastran_rod_stress:
            icase = get_rod_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=True)
        if settings.nastran_bar_stress:
            icase = get_bar_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=True)
        if settings.nastran_beam_stress:
            icase = get_beam_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=True)

        icase = get_solid_stress_strains(
            eids, cases, model, times, key, icase,
            form_dict, header_dict, keys_map, is_stress=True)

        icase = get_spring_stress_strains(
            eids, cases, model, times, key, icase,
            form_dict, header_dict, keys_map, is_stress=True)


        return icase


    def _fill_op2_centroidal_force(self, cases, model, times, key, icase,
                                   force_dict, header_dict, keys_map) -> int:
        """Creates the time accurate force objects"""
        for itime, unused_dt in enumerate(times):
            try:
                icase = self._fill_op2_time_centroidal_force(
                    cases, model, key, icase, itime,
                    force_dict, header_dict, keys_map)
            except IndexError:
                self.log.error('problem getting force...')
                break
        return icase

    def _fill_op2_centroidal_strain(self, cases, model, times, key, icase,
                                    form_dict, header_dict, keys_map) -> int:
        """Creates the time accurate strain objects"""
        settings = self.settings  # typpe: Settings
        if settings.nastran_strain:
            for itime, unused_dt in enumerate(times):
                try:
                    icase = self._fill_op2_time_centroidal_stress(
                        cases, model, key, icase, itime, form_dict, header_dict, keys_map,
                        is_stress=False)
                except IndexError:
                    self.log.error('problem getting strain...')
                    break

        eids = self.element_ids
        if settings.nastran_composite_plate_strain:
            icase = get_plate_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=False)
            icase = get_plate_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=False,
                prefix='modal_contribution',
            )

        if settings.nastran_composite_plate_strain:
            icase = get_composite_plate_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map,
                self.strain[key].composite_data_dict, is_stress=False)

        if settings.nastran_rod_strain:
            icase = get_rod_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=False)
        if settings.nastran_bar_strain:
            icase = get_bar_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=False)
        if settings.nastran_beam_strain:
            icase = get_beam_stress_strains(
                eids, cases, model, times, key, icase,
                form_dict, header_dict, keys_map, is_stress=False)

        icase = get_solid_stress_strains(
            eids, cases, model, times, key, icase,
            form_dict, header_dict, keys_map, is_stress=False)
        icase = get_spring_stress_strains(
            eids, cases, model, times, key, icase,
            form_dict, header_dict, keys_map, is_stress=False)

        return icase

    def _fill_op2_time_centroidal_stress(self, cases, model: OP2,
                                         key, icase: int, itime: int,
                                         form_dict: Dict[Any, Any],
                                         header_dict: Dict[Any, Any],
                                         keys_map: Dict[Any, Any],
                                         is_stress=True) -> int:
        """Creates the time accurate stress objects"""

        #new_cases = True
        #assert isinstance(subcase_id, int), type(subcase_id)
        assert isinstance(icase, int), icase
        #assert isinstance(itime, int), type(itime)
        assert is_stress in [True, False], is_stress
        eids = self.element_ids
        assert len(eids) > 0, eids
        nelements = self.nelements

        is_element_on = np.zeros(nelements, dtype='int8')  # is the element supported
        oxx = np.full(nelements, np.nan, dtype='float32')
        oyy = np.full(nelements, np.nan, dtype='float32')
        ozz = np.full(nelements, np.nan, dtype='float32')

        txy = np.full(nelements, np.nan, dtype='float32')
        tyz = np.full(nelements, np.nan, dtype='float32')
        txz = np.full(nelements, np.nan, dtype='float32')

        max_principal = np.full(nelements, np.nan, dtype='float32')  # max
        mid_principal = np.full(nelements, np.nan, dtype='float32')  # mid
        min_principal = np.full(nelements, np.nan, dtype='float32')  # min
        #max_shear = np.full(nelements, np.nan, dtype='float32')
        ovm = np.full(nelements, np.nan, dtype='float32')

        vm_word = None
        #-------------------------------------------------------------
        #vm_word = get_spring_stress_strain(
            #model, key, is_stress, vm_word, itime,
            #oxx, txy,
            #max_principal, min_principal, ovm, is_element_on,
            #eids, header_dict, keys_map)

        #-------------------------------------------------------------
        vm_word = get_rod_stress_strain(
            model, key, is_stress, vm_word, itime,
            oxx, txy,
            max_principal, min_principal, ovm, is_element_on,
            eids, header_dict, keys_map)

        vm_word = get_bar_stress_strain(
            model, key, is_stress, vm_word, itime,
            oxx,
            max_principal, min_principal, ovm, is_element_on,
            eids, header_dict, keys_map)

        vm_word = get_bar100_stress_strain(
            model, key, is_stress, vm_word, itime,
            oxx,
            max_principal, min_principal, ovm, is_element_on,
            eids, header_dict, keys_map)

        vm_word = get_beam_stress_strain(
            model, key, is_stress, vm_word, itime,
            oxx,
            max_principal, min_principal, ovm, is_element_on,
            header_dict, keys_map, self.eid_map)
        #-------------------------------------------------------------
        vm_word = get_plate_stress_strain(
            model, key, is_stress, vm_word, itime,
            oxx, oyy, txy, max_principal, min_principal, ovm, is_element_on,
            eids, header_dict, keys_map)

        #vm_word = get_shear_stress_strain(
            #model, key, is_stress, vm_word, itime,
            #oxx, txy,
            #max_principal, min_principal, ovm, is_element_on,
            #eids, header_dict, keys_map)

        if is_stress:
            stress_obj = self.stress[key]
        else:
            stress_obj = self.strain[key]

        if len(stress_obj.composite_data_dict):
            str(stress_obj)
            vm_word = stress_obj.set_composite_stress_old(
                key, itime, oxx, oyy, txy, tyz, txz,
                max_principal, min_principal, ovm,
                is_element_on, header_dict,
            )

        vm_word = get_solid_stress_strain(
            model, key, is_stress, vm_word, itime,
            oxx, oyy, ozz, txy, tyz, txz,
            max_principal, mid_principal, min_principal, ovm, is_element_on,
            eids, header_dict, keys_map)

        if is_stress:
            word = 'Stress'
            fmt = '%.3f'
        else:
            word = 'Strain'
            fmt = '%.4e'

        # a form is the table of output...
        # Subcase 1         <--- formi  - form_isubcase
        #    Time 1
        #        Stress     <--- form0  - the root level
        #            oxx    <--- formis - form_itime_stress
        #            oyy
        #            ozz

        if vm_word is None:
            #print('vm_word is None')
            return icase

        form0 = (word, None, [])
        unused_formis = form0[2]
        subcase_id = key[2]
        header = header_dict[(key, itime)]
        formi = []
        form_dict[(key, itime)].append(('Combined ' + word, None, formi))

        if is_stress and itime == 0:
            if is_element_on.min() == 0:  # if all elements aren't on
                print_empty_elements(self.model, eids, is_element_on, self.log_error)

                is_element_on = np.isfinite(oxx)
                is_element_on = is_element_on.astype('|i1')
                stress_res = GuiResult(
                    subcase_id, header=f'Stress - isElementOn: {header}', title='Stress\nisElementOn',
                    location='centroid', scalar=is_element_on, mask_value=0, data_format=fmt)

                cases[icase] = (stress_res, (subcase_id, 'Stress - isElementOn'))
                formi.append(('Stress - IsElementOn', icase, []))
                icase += 1

        #print('max/min', max_principal.max(), max_principal.min())
        # header = _get_nastran_header(case, dt, itime)
        if np.any(np.isfinite(oxx)):
            oxx_res = GuiResult(subcase_id, header=word + f'XX: {header}', title=word + 'XX',
                                location='centroid', scalar=oxx, data_format=fmt)
            cases[icase] = (oxx_res, (subcase_id, word + 'XX'))
            formi.append((word + 'XX', icase, []))
            icase += 1

        if np.any(np.isfinite(oyy)):
            oyy_res = GuiResult(subcase_id, header=word + f'YY: {header}', title=word + 'YY',
                                location='centroid', scalar=oyy, data_format=fmt)
            cases[icase] = (oyy_res, (subcase_id, word + 'YY'))
            formi.append((word + 'YY', icase, []))
            icase += 1

        if np.any(np.isfinite(ozz)):
            ozz_res = GuiResult(subcase_id, header=word + f'ZZ: {header}', title=word + 'ZZ',
                                location='centroid', scalar=ozz, data_format=fmt)
            cases[icase] = (ozz_res, (subcase_id, word + 'ZZ'))
            formi.append((word + 'ZZ', icase, []))
            icase += 1

        if np.any(np.isfinite(txy)):
            oxy_res = GuiResult(subcase_id, header=word + f'XY: {header}', title=word + 'XY',
                                location='centroid', scalar=txy, data_format=fmt)
            cases[icase] = (oxy_res, (subcase_id, word + 'XY'))
            formi.append((word + 'XY', icase, []))
            icase += 1

        if np.any(np.isfinite(tyz)):
            oyz_res = GuiResult(subcase_id, header=word + f'YZ: {header}', title=word + 'YZ',
                                location='centroid', scalar=tyz, data_format=fmt)
            cases[icase] = (oyz_res, (subcase_id, word + 'YZ'))
            formi.append((word + 'YZ', icase, []))
            icase += 1

        if np.any(np.isfinite(txz)):
            oxz_res = GuiResult(subcase_id, header=word + f'XZ: {header}', title=word + 'XZ',
                                location='centroid', scalar=txz, data_format=fmt)
            cases[icase] = (oxz_res, (subcase_id, word + 'XZ'))
            formi.append((word + 'XZ', icase, []))
            icase += 1

        if np.any(np.isfinite(max_principal)):
            maxp_res = GuiResult(subcase_id, header=f'MaxPrincipal: {header}', title='MaxPrincipal',
                                 location='centroid', scalar=max_principal, data_format=fmt)
            cases[icase] = (maxp_res, (subcase_id, 'MaxPrincipal'))
            formi.append(('Max Principal', icase, []))
            icase += 1

        if np.any(np.isfinite(mid_principal)):
            midp_res = GuiResult(subcase_id, header=f'MidPrincipal: {header}', title='MidPrincipal',
                                 location='centroid', scalar=mid_principal, data_format=fmt)
            cases[icase] = (midp_res, (subcase_id, 'MidPrincipal'))
            formi.append(('Mid Principal', icase, []))
            icase += 1

        if np.any(np.isfinite(min_principal)):
            minp_res = GuiResult(subcase_id, header=f'MinPrincipal: {header}', title='MinPrincipal',
                                 location='centroid', scalar=min_principal, data_format=fmt)
            cases[icase] = (minp_res, (subcase_id, 'MinPrincipal'))
            formi.append(('Min Principal', icase, []))
            icase += 1

        if vm_word is not None:
            ovm_res = GuiResult(subcase_id, header=f'{vm_word}: {header}', title=vm_word,
                                location='centroid', scalar=ovm, data_format=fmt)
            cases[icase] = (ovm_res, (subcase_id, vm_word))
            formi.append((vm_word, icase, []))
            icase += 1

        #, case, header, form0
        return icase

def fill_responses(cases, model: OP2, icase):
    """adds the optimization responses"""
    form_optimization = []
    #fractional_mass_response = model.op2_results.responses.fractional_mass_response
    #if fractional_mass_response is not None:
        #print(fractional_mass_response)

    des_filename = model.des_filename
    if os.path.exists(des_filename):
        des_desvars = read_des_filename(des_filename)
        if des_desvars:
            subcase_id = 0
            #eids = des_desvars['eids']
            fractional_mass = des_desvars['fractional_mass']
            minp_res = GuiResult(subcase_id, header=f'Fractional Mass', title='% Mass',
                                 location='centroid', scalar=fractional_mass, ) # data_format=fmt
            cases[icase] = (minp_res, (subcase_id, 'Fractional Mass'))
            form_optimization.append(('Fractional Mass', icase, []))
            icase += 1
    #f06_filename = model.f06_filename
    #print('f06_filename =', f06_filename)
    #from pyNastran.f06.dev.read_sol_200 import read_sol_200
    #read_sol_200(f06_filename)

    #desvars = model.op2_results.responses.desvars  # type: Desvars
    #if desvars is not None:
        #itop = np.where(desvars.label == 'TOPVAR')[0]
        #if len(itop):
            #print(desvars)
            #print('itop =', itop)
            #asdf
            #form_optimization.append(('TOPVAR', icase, []))

        #minp_res = GuiResult(subcase_id, header=f'MinPrincipal: {header}', title='MinPrincipal',
                             #location='centroid', scalar=min_principal, data_format=fmt)
        #cases[icase] = (minp_res, (subcase_id, 'MinPrincipal'))

        #desvars.internal_id = np.zeros(ndesvars, dtype='int32')
        #desvars.desvar_id = np.zeros(ndesvars, dtype='int32')
        #desvars.label = np.zeros(ndesvars, dtype='|U8')
        #desvars.lower = np.zeros(ndesvars, dtype='float32')
        #desvars.upper = np.zeros(ndesvars, dtype='float32')
        #desvars.delxv = np.zeros(ndesvars, dtype='float32')
        #desvars.dunno = np.zeros(ndesvars, dtype='float32')
    return icase, form_optimization

def _fill_nastran_displacements(cases, model: OP2, key, icase: int,
                                form_dict, header_dict, keys_map,
                                xyz_cid0,
                                nnodes: int, node_ids, log, dim_max: float=1.0) -> int:
    """
    loads the nodal dispalcements/velocity/acceleration/eigenvector/spc/mpc forces
    """
    displacement_like = [
        # slot, name, deflects

        # TODO: what is a velocity/acceleration?
        #       is it a fringe, displacement, force?
        (model.displacements, 'Displacement', True),
        (model.velocities, 'Velocity', False),
        (model.accelerations, 'Acceleration', False),
        (model.eigenvectors, 'Eigenvectors', True),
        (model.spc_forces, 'SPC Forces', False),
        (model.mpc_forces, 'MPC Forces', False),

        (model.load_vectors, 'LoadVectors', False),
        (model.applied_loads, 'AppliedLoads', False),
        (model.force_vectors, 'ForceVectors', False),
    ]

    for (result, name, deflects) in displacement_like:
        if key not in result:
            continue
        for t123_offset in [0, 3]:
            #if t123_offset == 3:
                #continue
            try:
                icase = _fill_nastran_ith_displacement(
                    result, name, deflects, t123_offset,
                    cases, model, key, icase,
                    form_dict, header_dict, keys_map,
                    xyz_cid0,
                    nnodes, node_ids, log, dim_max=dim_max)
            except ValueError:
                if not t123_offset == 3:
                    raise
                log.error('skipping %s result; t123_offset=%s; type=%s' % (
                    name, t123_offset, result[key].__class__.__name__))
    return icase

def _fill_nastran_ith_displacement(result, name: str, deflects: bool, t123_offset,
                                   cases, model: OP2, key, icase: int,
                                   form_dict: Dict[Tuple[Any, Any], str],
                                   header_dict: Dict[Tuple[Any, Any], str],
                                   keys_map: Dict[str, Any],
                                   xyz_cid0,
                                   nnodes: int, node_ids, log, dim_max: float=1.0) -> int:
    """helper for ``_fill_nastran_displacements`` to unindent the code a bit"""
    if t123_offset == 0:
        title1 = name + ' T_XYZ'
    else:
        assert t123_offset == 3, t123_offset
        title1 = name + ' R_XYZ'
    #title2 = name + ' R_XYZ'

    case = result[key]
    subcase_idi = case.isubcase
    if not hasattr(case, 'data'):
        print('str(%s) has no data...' % case.__class.__name__)
        return icase

    if not case.is_sort1:
        log.warning('Skipping because SORT2\n' + str(case))
        return icase

    t123, tnorm, ntimes = _get_t123_tnorm(case, node_ids, nnodes,
                                          t123_offset=t123_offset)

    titles = []
    scales = []
    headers = []
    #if deflects:
    if deflects:
        nastran_res = DisplacementResults(subcase_idi, titles, headers,
                                          xyz_cid0, t123, tnorm,
                                          scales,
                                          uname='NastranResult')

        #dmax = []
        for itime in range(ntimes):
            dt = case._times[itime]

            #if name == 'Displacement':
                # (6673, )
                #normiii = np.linalg.norm(t123[itime, :, :], axis=1)
                #print(normiii.shape)
                #print('Displacement; itime=%s time=%s tnorm=%s' % (
                    #itime, dt, normiii.max()))
                #dmax.append(normiii.max())

            tnorm_abs_max = get_tnorm_abs_max(case, t123, tnorm, itime)

            # mode = 2; freq = 75.9575 Hz
            header = _get_nastran_header(case, dt, itime)
            header_dict[(key, itime)] = header
            keys_map[key] = (case.subtitle, case.label,
                             case.superelement_adaptivity_index, case.pval_step)

            #if tnorm_abs_max == 0.0:
                #scale = self.displacement_scale_factor
            #else:
                #scale = self.displacement_scale_factor / tnorm_abs_max

            scale = dim_max
            if tnorm_abs_max > 0.0:
                scale = dim_max / tnorm_abs_max * 0.10
            scales.append(scale)
            titles.append(title1)
            headers.append(f'{title1}: {header}')
            cases[icase] = (nastran_res, (itime, title1))  # do I keep this???
            formii = (title1, icase, [])
            form_dict[(key, itime)].append(formii)
            icase += 1

        #if name == 'Displacement':
            # Displacement; itime=361 time=3.61 tnorm=1.46723
            #print('dmax = ', max(dmax))
            #pass
        nastran_res.save_defaults()
    else:
        nastran_res = ForceTableResults(subcase_idi, titles, headers,
                                        t123, tnorm,
                                        scales, #deflects=deflects,
                                        uname='NastranResult')
        for itime in range(ntimes):
            dt = case._times[itime]
            header = _get_nastran_header(case, dt, itime)
            header_dict[(key, itime)] = header
            keys_map[key] = (case.subtitle, case.label,
                             case.superelement_adaptivity_index, case.pval_step)

            #tnorm_abs_max = get_tnorm_abs_max(case, t123, tnorm, itime)
            #tnorm_abs_max = tnorm.max()
            scale = 1.
            scales.append(scale)
            titles.append(title1)
            headers.append(f'{title1}: {header}')
            cases[icase] = (nastran_res, (itime, title1))  # do I keep this???
            formii = (title1, icase, [])
            form_dict[(key, itime)].append(formii)
            icase += 1
        nastran_res.save_defaults()
    return icase

def _fill_nastran_temperatures(cases, model: OP2, key, icase: int,
                               form_dict, header_dict, keys_map, nnodes: int, log) -> int:
    """loads the nodal temperatures"""
    #nids = self.node_ids
    temperature_like = [
        (model.temperatures, 'Temperature'),
    ]
    for (result, name) in temperature_like:
        if key not in result:
            continue
        case = result[key]
        subcase_idi = case.isubcase
        if not hasattr(case, 'data'):
            continue

        if not case.is_sort1:
            log.warning('Skipping because SORT2\n' + str(case))
            continue
        assert case.is_sort1, case.is_sort1

        ntimes = case.ntimes
        for itime in range(ntimes):
            dt = case._times[itime]
            header = _get_nastran_header(case, dt, itime)
            header_dict[(key, itime)] = header
            keys_map[key] = (case.subtitle, case.label,
                             case.superelement_adaptivity_index, case.pval_step)

            loads = case.data[itime, :, :]
            nxyz = norm(loads[:, :3], axis=1)
            assert len(nxyz) == nnodes, 'len(nxyz)=%s nnodes=%s' % (
                len(nxyz), nnodes)

            temp_res = GuiResult(subcase_idi, header=f'{name}: {header}', title=name,
                                 location='node', scalar=loads[:, 0])
            cases[icase] = (temp_res, (0, name))
            form_dict[(key, itime)].append((name, icase, []))
            icase += 1
    return icase

def print_empty_elements(model, element_ids, is_element_on, log_error):
    """prints the first 20 elements that aren't supportedas part of the stress results"""
    ioff = np.where(is_element_on == 0)[0]
    print('stress_eids_off = %s' % np.array(element_ids[ioff]))
    log_error('stress_eids_off = %s' % element_ids[ioff])

    i = 0
    imax = 20
    for eid in element_ids[ioff]:
        element = model.elements[eid]
        if element.type not in ['CDAMP1', 'CDAMP2', 'CDAMP3', 'CDAMP4']:
            print(element.rstrip())
            i += 1
            if i == imax:
                break
    print('-----------------------------------')



def _get_t123_tnorm(case, nids, nnodes: int, t123_offset: int=0):
    """
    helper method for _fill_op2_oug_oqg

    Parameters
    ----------
    case : DisplacementArray, ForceArray, etc.
        the OP2 result object???
    nids : (nnodes,) int ndarray
        the nodes in the model???
    nnodes : int
        the number of nodes in the model???
    t123_offset : int; default=0
        0 : translations / forces
        3 : rotations / moments

    Returns
    -------
    t123 : (ntimes, nnodes, 3) float ndarray
       the translations or rotations
    tnorm : (ntimes, 3) float ndarray
        ???
    ntimes : int
       number of times
    """
    assert case.is_sort1, case.is_sort1

    itime0 = 0
    t1 = case.data[itime0, :, 0]
    ndata = t1.shape[0]
    if nnodes != ndata:
        #print('nnodes=%s ndata=%s' % (nnodes, ndata))
        nidsi = case.node_gridtype[:, 0]
        #assert len(nidsi) == nnodes, 'nidsi=%s nnodes=%s' % (nidsi, nnodes)
        j = np.searchsorted(nids, nidsi)  # searching for nidsi

        try:
            if not np.allclose(nids[j], nidsi):
                msg = 'nids[j]=%s nidsi=%s' % (nids[j], nidsi)
                raise RuntimeError(msg)
        except IndexError:
            msg = 'node_ids = %s\n' % list(nids)
            msg += 'nidsi in disp = %s\n' % list(nidsi)
            raise IndexError(msg)

    # (itime, nnodes, xyz)
    # (901, 6673, 3)
    t123 = case.data[:, :, t123_offset:t123_offset+3]
    ntimes = case.ntimes

    if nnodes != ndata:
        dtype = t123.dtype.name
        t123i = np.zeros((ntimes, nnodes, 3), dtype=dtype)
        t123i[:, j, :] = t123
        t123 = t123i

        # (itime, nnodes, xyz)
        # tnorm (901, 3)
        tnorm = norm(t123, axis=2)   # I think this is wrong...
        #print('tnorm.shape ', tnorm.shape)
        assert len(tnorm) == t123.shape[0]
    else:
        # (itime, nnodes, xyz)
        # tnorm (901, 3)

        # float32s are apparently buggy in numpy if you have small numbers
        # see models/elements/loadstep_elememnts.op2
        try:
            tnorm = norm(t123, axis=1)
        except FloatingPointError:
            dtype_map = {
                'float32': 'float64',
                'complex64': 'complex128',
            }
            dtype = dtype_map[t123.dtype.name]
            t123 = t123.astype(dtype=dtype)
            tnorm = norm(t123, axis=1)

            #print('skipping %s' % name)
            #print(t123.max(axis=1))
            #for itime, ti in enumerate(t123):
                #print('itime=%s' % itime)
                #print(ti.tolist())
        assert len(tnorm) == t123.shape[0]

    assert t123.shape[0] == ntimes, 'shape=%s expected=(%s, %s, 3)' % (t123.shape, ntimes, nnodes)
    assert t123.shape[1] == nnodes, 'shape=%s expected=(%s, %s, 3)' % (t123.shape, ntimes, nnodes)
    return t123, tnorm, ntimes


def _get_times(model, key):
    """
    Get the times/frequencies/eigenvalues/loadsteps used on a given
    subcase
    """
    table_types = model.get_table_types()
    is_real = True
    is_data = False
    is_static = False
    times = None
    for table_type in table_types:
        if not model.has_result(table_type) or table_type.startswith('responses.'):
            #model.log.debug('no table_type=%s' % table_type)
            continue

        table = model.get_result(table_type)
        if len(table) == 0:
            continue
        #print(key, table, type(table))

        if key in table:
            is_data = True
            case = table[key]
            #print(case)
            is_real = case.is_real

            # you're presumably looking here because of a bug
            # are you sure the keys are the right length?
            #print("is_real=%r nonlinear_factor=%r _times=%s" % (
                #is_real, case.nonlinear_factor, case._times))
            if case.nonlinear_factor is not None:
                times = case._times
                #print(case)
                #print(times)
                is_static = False
            else:
                is_static = True
                times = np.zeros(1, dtype='int32')
            #print('times = ', times)
            break
            #return is_data, is_static, is_real, times
    return is_data, is_static, is_real, times

def get_tnorm_abs_max(case, t123, tnorm, itime):
    """
    The normalization value is consistent for static, frequency, transient,
    and load step cases, but is independent for modal cases.
    """
    if case.analysis_code in [1, 5, 6, 10, 11]:
        # dependent
        # 1-statics
        # 5-frequency
        # 6-transient
        # 10-nonlinear statics
        # 11-old nonlinear statics
        tnorm_abs_max = tnorm.max()
    elif case.analysis_code in [2, 7, 8, 9]:
        # independent
        # 2-eigenvectors
        # 7-pre-buckling
        # 8-post-buckling
        # 9-complex eigenvalues
        tnorm_abs_max = np.linalg.norm(t123[itime, :, :], axis=1).max()
    else:
        raise NotImplementedError(f'analysis_code={case.analysis_code}\ncase:\n{case}')
    return tnorm_abs_max

def read_des_filename(des_filename):
    """
    DESIGN CYCLE :    30
    1
    Topology Optimization Element Density Distribution
    Total number of element     3912
        1115       0
    0.1408992E-01
        1116       0
    0.1628276E-01
    """
    with open(des_filename, 'r') as des_file:
        lines = des_file.readlines()

    i = 0
    word, ncycles_str = lines[0].split(':')
    word = word.strip()
    assert word == 'DESIGN CYCLE'
    unused_ncycles = int(ncycles_str)
    i += 3
    assert lines[i].startswith('Total number of element'), lines[i]
    nelements = int(lines[i].split()[-1])
    i += 1

    eids = []
    fractional_mass = []
    for unused_ielement in range(nelements):
        #print(lines[i].strip())
        eid, zero = lines[i].split()
        frac = float(lines[i+1])
        assert zero == '0', lines[i].strip()
        eids.append(eid)
        fractional_mass.append(frac)
        i += 2
    eids = np.array(eids, dtype='int32')
    fractional_mass = np.array(fractional_mass, dtype='float32')
    desvars = {
        'eids' : eids,
        'fractional_mass' : fractional_mass,}
    return desvars


def _get_stress_table_types() -> List[str]:  # pragma: no cover
    """
    Gets the list of Nastran stress objects that the GUI supports
    """
    table_types = [
        # OES - tCode=5 thermal=0 s_code=0,1 (stress/strain)
        # OES - CELAS1/CELAS2/CELAS3/CELAS4 stress
        'celas1_stress',
        'celas2_stress',
        'celas3_stress',
        'celas4_stress',

        # OES - CELAS1/CELAS2/CELAS3/CELAS4 strain
        'celas1_strain',
        'celas2_strain',
        'celas3_strain',
        'celas4_strain',

        # OES - isotropic CROD/CONROD/CTUBE stress
        'crod_stress',
        'conrod_stress',
        'ctube_stress',

        # OES - isotropic CROD/CONROD/CTUBE strain
        'crod_strain',
        'conrod_strain',
        'ctube_strain',

        # OES - isotropic CBAR stress
        'cbar_stress',
        # OES - isotropic CBAR strain
        'cbar_strain',
        # OES - isotropic CBEAM stress
        'cbeam_stress',
        # OES - isotropic CBEAM strain
        'cbeam_strain',

        # OES - isotropic CTRIA3/CQUAD4 stress
        'ctria3_stress',
        'cquad4_stress',

        # OES - isotropic CTRIA3/CQUAD4 strain
        'ctria3_strain',
        'cquad4_strain',

        # OES - isotropic CTETRA/CHEXA/CPENTA stress
        'ctetra_stress',
        'chexa_stress',
        'cpenta_stress',

        # OES - isotropic CTETRA/CHEXA/CPENTA strain
        'ctetra_strain',
        'chexa_strain',
        'cpenta_strain',

        # OES - CSHEAR stress
        'cshear_stress',
        # OES - CSHEAR strain
        'cshear_strain',
        # OES - CEALS1 224, CELAS3 225
        'nonlinear_spring_stress',
        # OES - GAPNL 86
        'nonlinear_cgap_stress',
        # OES - CBUSH 226
        'nolinear_cbush_stress',
    ]

    table_types += [
        # OES - CTRIAX6
        'ctriax_stress',
        'ctriax_strain',

        'cbush_stress',
        'cbush_strain',
        'cbush1d_stress_strain',

        # OES - nonlinear CROD/CONROD/CTUBE stress
        'nonlinear_rod_stress',
        'nonlinear_rod_strain',

        # OESNLXR - CTRIA3/CQUAD4 stress
        'nonlinear_plate_stress',
        'nonlinear_plate_strain',
        #'hyperelastic_plate_stress',
        'hyperelastic_cquad4_strain',

        # OES - composite CTRIA3/CQUAD4 stress
        'cquad4_composite_stress',
        'cquad8_composite_stress',
        'ctria3_composite_stress',
        'ctria6_composite_stress',

        'cquad4_composite_strain',
        'cquad8_composite_strain',
        'ctria3_composite_strain',
        'ctria6_composite_strain',

        # OGS1 - grid point stresses
        'grid_point_surface_stresses',        # tCode=26
        'grid_point_volume_stresses',  # tCode=27
    ]
    return table_types

def _get_stress_times(model: OP2, isubcase: int) -> Tuple[bool, bool, bool, Any]: # pragma: no cover
    """Are there any stress/strain results?"""
    table_types = _get_stress_table_types()
    is_real = True
    is_data = False
    is_static = False
    times = None
    for table_type in table_types:
        if not hasattr(model, table_type):
            # print('no table_type=%s' % table_type)
            continue
        table = getattr(model, table_type)
        if isubcase in table:
            is_data = True
            case = table[isubcase]
            is_real = case.is_real
            if case.nonlinear_factor is not None:
                times = case._times
                is_static = False
            else:
                is_static = True
                times = np.zeros(1, dtype='int32')
            break
            #return is_data, is_static, is_real, times
    return is_data, is_static, is_real, times

def get_rod_stress_strains(eids, cases, model, times, key, icase,
                           form_dict, header_dict, keys_map, is_stress):
    """
    helper method for _fill_op2_time_centroidal_stress.
    """
    #print("***stress eids=", eids)
    subcase_id = key[0]
    if is_stress:
        rods = [
            model.crod_stress, model.conrod_stress, model.ctube_stress,
        ]
        word = 'Stress'
    else:
        rods = [
            model.crod_strain, model.conrod_strain, model.ctube_strain,
        ]
        word = 'Strain'

    #titles = []
    rod_cases = []
    rod_ieids = []
    for result in rods:
        if key not in result:
            continue
        case = result[key]

        eidsi = case.element
        i = np.searchsorted(eids, eidsi)
        if len(i) != len(np.unique(i)):
            #print(case.element_node)
            #print('element_name=%s nnodes_per_element=%s' % (case.element_name, nnodes_per_element))
            #print('iplate = %s' % i)
            #print('  eids = %s' % eids)
            #print('  eidsiA = %s' % case.element_node[:, 0])
            #print('  eidsiB = %s' % eidsi)
            msg = 'i%s (rod)=%s is not unique' % (case.element_name, str(i))
            #msg = 'iplate=%s is not unique' % str(i)
            model.log.warning(msg)
            continue
        if i.max() == len(eids):
            model.log.error('skipping because lookup is out of range...')
            continue
        #print('i =', i, i.max())
        #print('eids =', eids, len(eids))
        #print('eidsi =', eidsi, eids)
        #print(f'------------adding i={i} for {case.element_name}-----------')
        rod_cases.append(case)
        rod_ieids.append(i)
    if not rod_ieids:
        return icase

    rod_ieids = np.hstack(rod_ieids)
    ieid_max = len(eids)
    #print('ieid_max =', ieid_max)

    case = rod_cases[0]
    case_headers = case.get_headers()
    #print(case_headers)
    if is_stress:
        #sigma = 'σ'
        method_map = {
            'axial' : 'σxx',
            'torsion' : 'τxy',
            'SMa' : 'MS_axial',
            'SMt' : 'MS_torsion',
            #'omax' : 'σmax',
            #'omin' : 'σmin',
            #'von_mises' : 'σ von Mises',
        }
        data_format = '%.3f'
    else:
        #sigma = 'ϵ'
        method_map = {
            'axial' : 'ϵxx',
            'torsion' : 'ϵxy',
            'SMa' : 'MS_axial',
            'SMt' : 'MS_torsion',
            #'emax' : 'ϵmax',
            #'emin' : 'ϵmin',
            #'von_mises' : 'ϵ von Mises',
        }
        data_format = '%.3e'
    methods = [method_map[headeri] for headeri in case_headers]
    #if 'Mises' in methods:
        #methods.append('Max shear')
    #else:
        #methods.append(f'{sigma} von Mises')

    #if case.is_von_mises:
        #vm_word = 'vonMises'
    #else:
        #vm_word = 'maxShear'

    #headersi = case.get_headers()
    #print('headersi =', headersi)

    scalars_array = []
    for case in rod_cases:
        #if case.is_complex:
            #model.log.warning(f'skipping complex Rod {word}')
            #continue

        #ntimes, nelements, nresults = case.data.shape
        #self.data[self.itime, self.itotal, :] = [fd, oxx, oyy,
        #                                         txy, angle,
        #                                         majorP, minorP, ovm]

        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)

        #nnodes_per_element = case.nnodes
        #nelements_nnodes = nnodes_nlayers // 2
        #nelements = nelements_nnodes // nnodes_per_element
        #nlayers = 2
        scalars = case.data
        scalars_array.append(scalars)

    if len(scalars_array) == 0:
        return icase

    if len(scalars_array) == 1:
        scalars_array = scalars_array[0]
    else:
        scalars_array = np.concatenate(scalars_array, axis=1)

    #titles = []  # legend title
    headers = [] # sidebar word
    res = SimpleTableResults(
        subcase_id, headers, rod_ieids, ieid_max, scalars_array, methods,
        data_format=data_format,
        colormap='jet', uname='Rod ' + word)

    icase = _add_methods_to_form(icase, cases, key, subcase_id, word, res, case,
                                 form_dict, header_dict, methods,
                                 name='Rod')
    return icase

def get_bar_stress_strains(eids, cases, model, times, key, icase,
                           form_dict, header_dict, keys_map, is_stress):
    """
    helper method for _fill_op2_time_centroidal_stress.
    """
    #print("***stress eids=", eids)
    subcase_id = key[0]
    if is_stress:
        bars = [model.cbar_stress]
        word = 'Stress'
    else:
        bars = [model.cbar_strain]
        word = 'Strain'

    #titles = []
    bar_cases = []
    bar_ieids = []
    for result in bars:
        if key not in result:
            continue
        case = result[key]

        eidsi = case.element
        i = np.searchsorted(eids, eidsi)
        if len(i) != len(np.unique(i)):
            #print(case.element_node)
            #print('element_name=%s nnodes_per_element=%s' % (case.element_name, nnodes_per_element))
            #print('iplate = %s' % i)
            #print('  eids = %s' % eids)
            #print('  eidsiA = %s' % case.element_node[:, 0])
            #print('  eidsiB = %s' % eidsi)
            msg = 'i%s (bar)=%s is not unique' % (case.element_name, str(i))
            #msg = 'iplate=%s is not unique' % str(i)
            model.log.warning(msg)
            continue
        if i.max() == len(eids):
            model.log.error('skipping because lookup is out of range...')
            continue
        #print('i =', i, i.max())
        #print('eids =', eids, len(eids))
        #print('eidsi =', eidsi, eids)
        #print(f'------------adding i={i} for {case.element_name}-----------')
        bar_cases.append(case)
        bar_ieids.append(i)
    if not bar_ieids:
        return icase

    bar_ieids = np.hstack(bar_ieids)
    ieid_max = len(eids)
    #print('ieid_max =', ieid_max)

    case = bar_cases[0]
    case_headers = case.get_headers()
    #print(case_headers)
    if is_stress:
        #sigma = 'σ'
        method_map = {
             's1a' : 'σxx 1A',
             's2a' : 'σxx 2A',
             's3a' : 'σxx 3A',
             's4a' : 'σxx 4A',

             's1b' : 'σxx 1B',
             's2b' : 'σxx 2B',
             's3b' : 'σxx 3B',
             's4b' : 'σxx 4B',

            'axial' : 'σxx',
            'MS_tension' : 'MS_tension',
            'MS_compression' : 'MS_compression',
            'smaxa' : 'σmax A',
            'smina' : 'σmin A',
            'smaxb' : 'σmax B',
            'sminb' : 'σmin B',
            #'von_mises' : 'σ von Mises',
        }
        data_format = '%.3f'
    else:
        #sigma = 'ϵ'
        method_map = {
            'e1a' : 'ϵxx 1A',
            'e2a' : 'ϵxx 2A',
            'e3a' : 'ϵxx 3A',
            'e4a' : 'ϵxx 4A',

            'e1b' : 'ϵxx 1B',
            'e2b' : 'ϵxx 2B',
            'e3b' : 'ϵxx 3B',
            'e4b' : 'ϵxx 4B',


            'e1c': 'ϵxx 1C',
            'e1d': 'ϵxx 1D',
            'e2c': 'ϵxx 2C',
            'e2d': 'ϵxx 2D',

           'axial' : 'ϵxx',
           'MS_tension' : 'MS_tension',
           'MS_compression' : 'MS_compression',
           'emaxa' : 'ϵmax A',
           'emina' : 'ϵmin A',
           'emaxb' : 'ϵmax B',
           'eminb' : 'ϵmin B',
            #'emax' : 'ϵmax',
            #'emin' : 'ϵmin',
            #'von_mises' : 'ϵ von Mises',
        }
        data_format = '%.3e'
    methods = [method_map[headeri] for headeri in case_headers]
    #if 'Mises' in methods:
        #methods.append('Max shear')
    #else:
        #methods.append(f'{sigma} von Mises')

    #if case.is_von_mises:
        #vm_word = 'vonMises'
    #else:
        #vm_word = 'maxShear'

    #headersi = case.get_headers()
    #print('headersi =', headersi)

    scalars_array = []
    for case in bar_cases:
        if case.is_complex:
            model.log.warning(f'skipping complex Bar {word}')
            continue

        #ntimes, nelements, nresults = case.data.shape
        #self.data[self.itime, self.itotal, :] = [fd, oxx, oyy,
        #                                         txy, angle,
        #                                         majorP, minorP, ovm]

        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)

        #nnodes_per_element = case.nnodes
        #nelements_nnodes = nnodes_nlayers // 2
        #nelements = nelements_nnodes // nnodes_per_element
        #nlayers = 2
        scalars = case.data
        scalars_array.append(scalars)

    if len(scalars_array) == 0:
        return icase

    if len(scalars_array) == 1:
        scalars_array = scalars_array[0]
    else:
        scalars_array = np.concatenate(scalars_array, axis=1)

    #include_tension_margin = True
    #include_compression_margin = True
    itension = methods.index('MS_tension')
    icompression = methods.index('MS_compression')
    exclude_tension_margin = np.allclose(np.abs(scalars_array[:, :, itension]).max(), 0.0)
    exclude_compression_margin = np.allclose(np.abs(scalars_array[:, :, icompression]).max(), 0.0)
    iresults = list(range(len(methods)))
    if exclude_compression_margin:
        methods.pop(icompression)
        iresults.pop(icompression)
    if exclude_tension_margin:
        methods.pop(itension)
        iresults.pop(itension)
    if exclude_compression_margin or exclude_compression_margin:
        scalars_array = scalars_array[:, :, iresults]

    #titles = []  # legend title
    headers = [] # sidebar word
    res = SimpleTableResults(
        subcase_id, headers, bar_ieids, ieid_max, scalars_array, methods,
        data_format=data_format,
        colormap='jet', uname='Bar ' + word)

    icase = _add_methods_to_form(icase, cases, key, subcase_id, word, res, case,
                                 form_dict, header_dict, methods,
                                 name='Bar')
    return icase

def get_beam_stress_strains(eids, cases, model, times, key, icase,
                            form_dict, header_dict, keys_map, is_stress):
    """
    helper method for _fill_op2_time_centroidal_stress.
    """
    return icase
    #print("***stress eids=", eids)
    subcase_id = key[0]
    if is_stress:
        beams = [model.cbeam_stress]
        word = 'Stress'
    else:
        beams = [model.cbeam_strain]
        word = 'Strain'

    #titles = []
    beam_cases = []
    beam_ieids = []
    for result in beams:
        if key not in result:
            continue
        case = result[key]

        eidsi = np.unique(case.element_node[:, 0])
        print(eidsi)
        i = np.searchsorted(eids, eidsi)
        if len(i) != len(np.unique(i)):
            #print(case.element_node)
            #print('element_name=%s nnodes_per_element=%s' % (case.element_name, nnodes_per_element))
            #print('iplate = %s' % i)
            #print('  eids = %s' % eids)
            #print('  eidsiA = %s' % case.element_node[:, 0])
            #print('  eidsiB = %s' % eidsi)
            msg = 'i%s (beam)=%s is not unique' % (case.element_name, str(i))
            #msg = 'iplate=%s is not unique' % str(i)
            model.log.warning(msg)
            continue
        if i.max() == len(eids):
            model.log.error('skipping because lookup is out of range...')
            continue

        i2 = np.hstack([i, i + 1]).T.flatten()
        print('i =', i)
        print('i2 =', i2)
        #aa
        #print('i =', i, i.max())
        #print('eids =', eids, len(eids))
        #print('eidsi =', eidsi, eids)
        #print(f'------------adding i={i} for {case.element_name}-----------')
        beam_cases.append(case)
        beam_ieids.append(i2)
    if not beam_ieids:
        return icase

    beam_ieids = np.hstack(beam_ieids)
    #inid_max = len(nids)
    ieid_max = len(eids)
    print('ieid_max =', ieid_max)

    case = beam_cases[0]
    case_headers = case.get_headers()
    #print(case_headers)
    if is_stress:
        #sigma = 'σ'
        method_map = {
            'sxc' : 'σxx C',
            'sxd' : 'σxx D',
            'sxe' : 'σxx E',
            'sxf' : 'σxx F',
            #'torsion' : 'τxy',
            'MS_tension' : 'MS_tension',
            'MS_compression' : 'MS_compression',
            'smax' : 'σmax',
            'smin' : 'σmin',
            #'von_mises' : 'σ von Mises',
        }
    else:
        #sigma = 'ϵ'
        method_map = {
            'sxc' : 'ϵxx C',
            'sxd' : 'ϵxx D',
            'sxe' : 'ϵxx E',
            'sxf' : 'ϵxx F',

            'exc' : 'ϵxx C',
            'exd' : 'ϵxx D',
            'exe' : 'ϵxx E',
            'exf' : 'ϵxx F',
            #'torsion' : 'τxy',
            'MS_tension' : 'MS_tension',
            'MS_compression' : 'MS_compression',
            'smax' : 'ϵmax',
            'smin' : 'ϵmin',
            #'von_mises' : 'ϵ von Mises',
        }
    methods = [method_map[headeri] for headeri in case_headers]
    #if 'Mises' in methods:
        #methods.append('Max shear')
    #else:
        #methods.append(f'{sigma} von Mises')

    #if case.is_von_mises:
        #vm_word = 'vonMises'
    #else:
        #vm_word = 'maxShear'

    #headersi = case.get_headers()
    #print('headersi =', headersi)

    scalars_array = []
    for case in beam_cases:
        if case.is_complex:
            model.log.warning(f'skipping complex Beam {word}')
            continue

        #ntimes, nelements, nresults = case.data.shape
        #self.data[self.itime, self.itotal, :] = [fd, oxx, oyy,
        #                                         txy, angle,
        #                                         majorP, minorP, ovm]

        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)

        #nnodes_per_element = case.nnodes
        #nelements_nnodes = nnodes_nlayers // 2
        #nelements = nelements_nnodes // nnodes_per_element
        #nlayers = 2
        scalars = case.data
        scalars_array.append(scalars)

    if len(scalars_array) == 0:
        return icase

    if len(scalars_array) == 1:
        scalars_array = scalars_array[0]
    else:
        scalars_array = np.concatenate(scalars_array, axis=1)

    #titles = []  # legend title
    headers = [] # sidebar word
    res = SimpleTableResults(
        subcase_id, headers, beam_ieids, ieid_max, scalars_array, methods,
        data_formats=None,
        colormap='jet', location='node', uname='Beam ' + word)

    times = case._times
    for itime, dt in enumerate(times):
        #dt = case._times[itime]
        header = _get_nastran_header(case, dt, itime)
        header_dict[(key, itime)] = header

        formi = []
        form = form_dict[(key, itime)]
        form.append(('Beam ' + word, None, formi))
        # formi = form[0][2]

        for imethod, method in enumerate(methods):
            #cases[icase] = (res, (subcase_id, header))
            cases[icase] = (res, (subcase_id, (itime, imethod, header)))
            formi.append((method, icase, []))
            icase += 1
    return icase

def get_plate_stress_strains(eids, cases, model, times, key, icase,
                             form_dict, header_dict, keys_map, is_stress,
                             prefix=''):
    """
    helper method for _fill_op2_time_centroidal_stress.
    Gets the max/min stress for each layer.
    """
    #print("***stress eids=", eids)
    subcase_id = key[0]
    if prefix == 'modal_contribution':
        results = model.op2_results.modal_contribution
        preword = 'Modal Contribution '
    elif prefix == '':
        results = model
        preword = ''
    else:  # pragma: no cover
        raise NotImplementedError(prefix)

    if is_stress:
        plates = [
            results.ctria3_stress, results.cquad4_stress,
            results.ctria6_stress, results.cquad8_stress,
            results.ctriar_stress, results.cquadr_stress,
        ]
        word = preword + 'Stress'
    else:
        plates = [
            results.ctria3_strain, results.cquad4_strain,
            results.ctria6_strain, results.cquad8_strain,
            results.ctriar_strain, results.cquadr_strain,
        ]
        word = preword + 'Strain'

    #titles = []
    plate_cases = []
    plates_ieids = []
    #print('key =', key)
    for iplate, result in enumerate(plates):
        #if result:
        #print(f'keys[{iplate}] = {result.keys()}')
        if key not in result:
            continue
        case = result[key]
        #print(case)

        nnodes_per_element = case.nnodes
        nlayers_per_element = nnodes_per_element * 2
        eidsi = case.element_node[::nlayers_per_element, 0]  # ::2 is for layer skipping
        #print(case.element_name, eidsi)
        i = np.searchsorted(eids, eidsi)
        if len(i) != len(np.unique(i)):
            #print(case.element_node)
            #print('element_name=%s nnodes_per_element=%s' % (case.element_name, nnodes_per_element))
            #print('iplate = %s' % i)
            #print('  eids = %s' % eids)
            #print('  eidsiA = %s' % case.element_node[:, 0])
            #print('  eidsiB = %s' % eidsi)
            msg = 'i%s (plate)=%s is not unique' % (case.element_name, str(i))
            #msg = 'iplate=%s is not unique' % str(i)
            model.log.warning(msg)
            continue
        if i.max() == len(eids):
            model.log.error('skipping because lookup is out of range...')
            continue
        #print('i =', i, i.max())
        #print('eids =', eids, len(eids))
        #print('eidsi =', eidsi, eids)
        #print(f'------------adding i={i} for {case.element_name}-----------')
        plate_cases.append(case)
        plates_ieids.append(i)
    if not plates_ieids:
        return icase

    plates_ieids = np.hstack(plates_ieids)
    ieid_max = len(eids)
    #print('ieid_max =', ieid_max)

    case = plate_cases[0]
    case_headers = case.get_headers()
    #print(case_headers)
    if is_stress:
        #sigma = 'σ'
        method_map = {
            'fiber_distance' : 'FiberDistance',
            'oxx' : 'σxx',
            'oyy' : 'σyy',
            'txy' : 'τxy',
            'angle' : 'θ',
            'omax' : 'σmax',
            'omin' : 'σmin',
            'von_mises' : 'σ von Mises',
        }
    else:
        #sigma = 'ϵ'
        method_map = {
            'fiber_curvature' : 'FiberCurvature',
            'fiber_distance' : 'FiberDistance',
            'exx' : 'ϵxx',
            'eyy' : 'ϵyy',
            'exy' : 'ϵxy',
            'angle' : 'θ',
            'emax' : 'ϵmax',
            'emin' : 'ϵmin',
            'von_mises' : 'ϵ von Mises',
        }
    methods = [method_map[headeri] for headeri in case_headers]
    #if 'Mises' in methods:
        #methods.append('Max shear')
    #else:
        #methods.append(f'{sigma} von Mises')

    #if case.is_von_mises:
        #vm_word = 'vonMises'
    #else:
        #vm_word = 'maxShear'

    #headersi = case.get_headers()
    #print('headersi =', headersi)

    scalars_array = []
    for case in plate_cases:
        #if case.is_complex:
            #print(f"skipping {case.class_name} because it's complex")
            #model.log.warning(f"skipping {case.class_name} because it's complex")
            #continue

        ntimes, nnodes_nlayers, nresults = case.data.shape
        #self.data[self.itime, self.itotal, :] = [fd, oxx, oyy,
        #                                         txy, angle,
        #                                         majorP, minorP, ovm]

        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)

        nnodes_per_element = case.nnodes
        nelements_nnodes = nnodes_nlayers // 2
        nelements = nelements_nnodes // nnodes_per_element
        nlayers = 2
        scalars = case.data.reshape(ntimes, nelements, nnodes_per_element, nlayers, nresults)
        scalars_array.append(scalars[:, :, 0, :, :])

    if len(scalars_array) == 0:
        return icase

    if len(scalars_array) == 1:
        scalars_array = scalars_array[0]
    else:
        scalars_array = np.concatenate(scalars_array, axis=1)

    #titles = []  # legend title
    headers = [] # sidebar word
    res = LayeredTableResults(
        subcase_id, headers, plates_ieids, ieid_max, scalars_array, methods,
        data_formats=None,
        colormap='jet')

    times = case._times
    for itime, dt in enumerate(times):
        #dt = case._times[itime]
        header = _get_nastran_header(case, dt, itime)
        header_dict[(key, itime)] = header

        formi = []
        form = form_dict[(key, itime)]
        form.append(('Plate ' + word, None, formi))
        # formi = form[0][2]
        form_dict[(key, itime)] = form

        for ilayer in range(2):
            layer = f' Layer {ilayer+1}'
            form_layeri = []
            formi.append((layer, None, form_layeri))
            for imethod, method in enumerate(methods):
                #cases[icase] = (res, (subcase_id, header))
                cases[icase] = (res, (subcase_id, (itime, ilayer, imethod, header)))
                form_layeri.append((f'{method} ({layer})', icase, []))
                icase += 1
    return icase

def get_composite_plate_stress_strains(eids, cases, model, times, key, icase,
                                       form_dict, header_dict, keys_map,
                                       composite_data_dict, is_stress):
    """
    helper method for _fill_op2_time_centroidal_stress.
    Gets the stress/strain for each layer.
    """
    subcase_id = key[0]

    plates_ieids = []
    for element_type, composite_data in composite_data_dict.items():
        try:
            element_layer, ueids, data2, vm_word, ntimes, headers = composite_data[key]
        except KeyError:
            print(composite_data)
            raise


        #print(element_type, ueids)
        i = np.searchsorted(eids, ueids)
        if len(i) != len(np.unique(i)):
            model.log.error(f' duplicate eids for composite {element_type}...i={i} eids={eids} ueids={ueids}')
            continue
        plates_ieids.append(i)
        #for itime2, header in enumerate(headers):
            #header_dict[(key, itime2)] = header
            #asdf

    if not plates_ieids:
        return icase

    case_map = {
        # is_stress, element_name
        (True, 'CTRIA3') : model.ctria3_composite_stress,
        (False, 'CTRIA3') : model.ctria3_composite_strain,

        (True, 'CQUAD4') : model.cquad4_composite_stress,
        (False, 'CQUAD4') : model.cquad4_composite_strain,
    }
    case_dict = case_map[(is_stress, element_type)]
    case = case_dict[key]


    plates_ieids = np.hstack(plates_ieids)
    ieid_max = len(eids)
    #print('ieid_max =', ieid_max)

    case_headers = case.get_headers()
    #print('case_headers =', case_headers, vm_word)
    if is_stress:
        word = 'Stress'
        #sigma = 'σ'
        method_map = {
            'o11' : 'σ11',
            'o22' : 'σ22',
            't12' : 't12',
            't1z' : 'τ1z',
            't2z' : 'τ2z',
            'angle' : 'θ',
            'major' : 'σ major',
            'minor' : 'σ minor',
            'max_shear' : 'MaxShear',
            #'von_mises' : 'σ von Mises',
        }
    else:
        word = 'Strain'
        #sigma = 'ϵ'
        method_map = {
            'e11' : 'ϵ11',
            'e22' : 'ϵ22',
            'e12' : 'ϵ12',
            'e1z' : 'ϵ1z',
            'e2z' : 'ϵ2z',
            'angle' : 'θ',
            'major' : 'ϵ major',
            'minor' : 'ϵ minor',
            'max_shear' : 'MaxShear',
        }
    methods = [method_map[headeri] for headeri in case_headers]
    #methods = case_headers

    #if 'Mises' in methods:
        #methods.append('Max shear')
    #else:
        #methods.append(f'{sigma} von Mises')

    #headersi = case.get_headers()
    #print('headersi =', headersi)
    #titles = []

    scalars_array = []
    for element_type, composite_data in composite_data_dict.items():
        unused_element_layer, unused_ueids, data2, unused_vm_word, unused_ntimes, unused_headers = composite_data[key]
        scalars_array.append(data2)
    if len(scalars_array) == 0:
        return icase

    if len(scalars_array) == 1:
        scalars_array = scalars_array[0]
    else:
        try:
            scalars_array = np.concatenate(scalars_array, axis=1)
        except ValueError:
            for scalars in scalars_array:
                print(scalars.shape)
            raise


    #print('scalars_array.shape =', scalars_array.shape)
    unused_ntimes, unused_nelements, nlayers, unused_nresults = scalars_array.shape

    #titles = []  # legend title
    headers = [] # sidebar word
    res = LayeredTableResults(
        subcase_id, headers, plates_ieids, ieid_max, scalars_array, methods,
        data_formats=None,
        colormap='jet')

    #times = case._times
    for itime, dt in enumerate(times):
        #dt = case._times[itime]
        header = _get_nastran_header(case, dt, itime)
        header_dict[(key, itime)] = header

        formi = []
        form = form_dict[(key, itime)]
        form.append(('Composite Plate ' + word, None, formi))
        # formi = form[0][2]
        form_dict[(key, itime)] = form

        form_layers = {}
        for ilayer in range(nlayers):
            layer_name = f' Layer {ilayer+1}'
            form_layeri = []
            formi.append((layer_name, None, form_layeri))
            form_layers[layer_name] = form_layeri

        for imethod, method in enumerate(methods):
            for ilayer in range(nlayers):
                layer_name = f' Layer {ilayer+1}'
                form_layeri = form_layers[layer_name]
                #cases[icase] = (res, (subcase_id, header))
                cases[icase] = (res, (subcase_id, (itime, ilayer, imethod, header)))
                form_layeri.append((f'{method} ({layer_name})', icase, []))
                icase += 1
    return icase

def get_solid_stress_strains(eids, cases, model, times, key, icase,
                             form_dict, header_dict, keys_map, is_stress):
    """
    helper method for _fill_op2_time_centroidal_stress.
    """
    #print("***stress eids=", eids)
    subcase_id = key[0]
    results = model
    if is_stress:
        solids = [
            results.ctetra_stress, results.cpenta_stress, results.chexa_stress, # results.cpyram_stress,
        ]
        word = 'Stress'
    else:
        solids = [
            results.ctetra_strain, results.cpenta_strain, results.chexa_strain, # results.cpyram_strain,
        ]
        word = 'Strain'

    #titles = []
    solid_cases = []
    solid_ieids = []
    for result in solids:
        if key not in result:
            continue
        case = result[key]

        #print(case)
        nnodes = case.get_nnodes()
        all_eidsi = case.element_node[:, 0]
        nall_eidsi = len(all_eidsi)
        nelementsi = nall_eidsi // nnodes
        eidsi = all_eidsi.reshape(nelementsi, nnodes)[:, 0]

        i = np.searchsorted(eids, eidsi)
        if len(i) != len(np.unique(i)):
            #print(case.element_node)
            #print('element_name=%s nnodes_per_element=%s' % (case.element_name, nnodes_per_element))
            #print('iplate = %s' % i)
            #print('  eids = %s' % eids)
            #print('  eidsiA = %s' % case.element_node[:, 0])
            #print('  eidsiB = %s' % eidsi)
            msg = 'i%s (solid)=%s is not unique' % (case.element_name, str(i))
            #msg = 'iplate=%s is not unique' % str(i)
            model.log.warning(msg)
            continue
        if i.max() == len(eids):
            model.log.error('skipping because lookup is out of range...')
            continue
        #print('i =', i, i.max())
        #print('eids =', eids, len(eids))
        #print('eidsi =', eidsi, eids)
        #print(f'------------adding i={i} for {case.element_name}-----------')
        solid_cases.append(case)
        solid_ieids.append(i)
    if not solid_ieids:
        return icase

    solid_ieids = np.hstack(solid_ieids)
    ieid_max = len(eids)
    #print('ieid_max =', ieid_max)

    case = solid_cases[0]
    case_headers = case.get_headers()
    if is_stress:
        #sigma = 'σ'
        method_map = {
            'oxx' : 'σxx',
            'oyy' : 'σyy',
            'ozz' : 'σzz',
            'txy' : 'τxy',
            'tyz' : 'τyz',
            'txz' : 'τxz',

            'omax' : 'σmax',
            'omin' : 'σmin',
            'omid' : 'σmid',
            'von_mises' : 'σ von Mises',

            #'axial' : 'σxx',
            #'torsion' : 'τxy',
            #'SMa' : 'MS_axial',
            #'SMt' : 'MS_torsion',
            #'von_mises' : 'σ von Mises',
        }
        data_format = '%.3f'
    else:
        #sigma = 'ϵ'
        method_map = {
            'exx' : 'ϵxx',
            'eyy' : 'ϵyy',
            'ezz' : 'ϵzz',
            'exy' : 'ϵxy',
            'eyz' : 'ϵyz',
            'exz' : 'ϵxz',

            'emax' : 'ϵmax',
            'emin' : 'ϵmin',
            'emid' : 'ϵmid',
            'von_mises' : 'ϵ von Mises',
        }
        data_format = '%.3e'
    methods = [method_map[headeri] for headeri in case_headers]
    #if 'Mises' in methods:
        #methods.append('Max shear')
    #else:
        #methods.append(f'{sigma} von Mises')

    #if case.is_von_mises:
        #vm_word = 'vonMises'
    #else:
        #vm_word = 'maxShear'

    #headersi = case.get_headers()
    #print('headersi =', headersi)

    scalars_array = []
    for case in solid_cases:
        #if case.is_complex:
            #model.log.warning(f'skipping complex Rod {word}')
            #continue

        #ntimes, nelements, nresults = case.data.shape
        #self.data[self.itime, self.itotal, :] = [fd, oxx, oyy,
        #                                         txy, angle,
        #                                         majorP, minorP, ovm]

        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)

        #nnodes_per_element = case.nnodes
        #nelements_nnodes = nnodes_nlayers // 2
        #nelements = nelements_nnodes // nnodes_per_element
        #nlayers = 2
        nnodes = case.get_nnodes()
        scalars = case.data
        #print('scalars.shape', scalars.shape)
        ntimes, nall, nresults = scalars.shape
        nelements = nall // nnodes
        scalars_save = scalars.reshape(ntimes, nelements, nnodes, nresults)
        scalars_array.append(scalars_save[:, :, 0, :])

    if len(scalars_array) == 0:
        return icase

    if len(scalars_array) == 1:
        scalars_array = scalars_array[0]
    else:
        scalars_array = np.concatenate(scalars_array, axis=1)

    #titles = []  # legend title
    headers = [] # sidebar word
    res = SimpleTableResults(
        subcase_id, headers, solid_ieids, ieid_max, scalars_array, methods,
        data_format=data_format,
        colormap='jet', uname='Solid ' + word)

    icase = _add_methods_to_form(icase, cases, key, subcase_id, word, res, case,
                                 form_dict, header_dict, methods,
                                 name='Solid')
    return icase

def get_spring_stress_strains(eids, cases, model, times, key, icase,
                              form_dict, header_dict, keys_map, is_stress):
    """
    helper method for _fill_op2_time_centroidal_stress.
    """
    #print("***stress eids=", eids)
    subcase_id = key[0]
    results = model
    if is_stress:
        springs = [results.celas1_stress, results.celas2_stress, results.celas3_stress, results.celas4_stress]
        word = 'Stress'
    else:
        springs = [results.celas1_strain, results.celas2_strain, results.celas3_strain, results.celas4_strain]
        word = 'Strain'

    #titles = []
    spring_cases = []
    spring_ieids = []
    for result in springs:
        if key not in result:
            continue
        case = result[key]

        #print(case)
        #nnodes = case.get_nnodes()
        #all_eidsi = case.element_node[:, 0]
        #nall_eidsi = len(all_eidsi)
        eidsi = case.element
        #nelementsi = nall_eidsi // nnodes
        #eidsi = all_eidsi.reshape(nelementsi, nnodes)[:, 0]

        i = np.searchsorted(eids, eidsi)
        if len(i) != len(np.unique(i)):
            #print(case.element_node)
            #print('element_name=%s nnodes_per_element=%s' % (case.element_name, nnodes_per_element))
            #print('iplate = %s' % i)
            #print('  eids = %s' % eids)
            #print('  eidsiA = %s' % case.element_node[:, 0])
            #print('  eidsiB = %s' % eidsi)
            msg = 'i%s (gap)=%s is not unique' % (case.element_name, str(i))
            #msg = 'iplate=%s is not unique' % str(i)
            model.log.warning(msg)
            continue
        if i.max() == len(eids):
            model.log.error('skipping because lookup is out of range...')
            continue
        #print('i =', i, i.max())
        #print('eids =', eids, len(eids))
        #print('eidsi =', eidsi, eids)
        #print(f'------------adding i={i} for {case.element_name}-----------')
        spring_cases.append(case)
        spring_ieids.append(i)
    if not spring_ieids:
        return icase

    spring_ieids = np.hstack(spring_ieids)
    ieid_max = len(eids)
    #print('ieid_max =', ieid_max)

    case = spring_cases[0]
    case_headers = case.get_headers()
    if is_stress:
        #sigma = 'σ'
        method_map = {
            'spring_stress' : 'σxx',
            #'oyy' : 'σyy',
            #'ozz' : 'σzz',
            #'txy' : 'τxy',
            #'tyz' : 'τyz',
            #'txz' : 'τxz',

            #'omax' : 'σmax',
            #'omin' : 'σmin',
            #'omid' : 'σmid',
            #'von_mises' : 'σ von Mises',

            #'axial' : 'σxx',
            #'torsion' : 'τxy',
            #'SMa' : 'MS_axial',
            #'SMt' : 'MS_torsion',
            #'von_mises' : 'σ von Mises',
        }
        data_format = '%.3f'
    else:
        #sigma = 'ϵ'
        method_map = {
            'spring_strain' : 'ϵxx',
            #'eyy' : 'ϵyy',
            #'ezz' : 'ϵzz',
            #'exy' : 'ϵxy',
            #'eyz' : 'ϵyz',
            #'exz' : 'ϵxz',

            #'emax' : 'ϵmax',
            #'emin' : 'ϵmin',
            #'emid' : 'ϵmid',
            #'von_mises' : 'ϵ von Mises',
        }
        data_format = '%.3e'
    methods = [method_map[headeri] for headeri in case_headers]
    #if 'Mises' in methods:
        #methods.append('Max shear')
    #else:
        #methods.append(f'{sigma} von Mises')

    #if case.is_von_mises:
        #vm_word = 'vonMises'
    #else:
        #vm_word = 'maxShear'

    #headersi = case.get_headers()
    #print('headersi =', headersi)

    scalars_array = []
    for case in spring_cases:
        #if case.is_complex:
            #model.log.warning(f'skipping complex Rod {word}')
            #continue

        #ntimes, nelements, nresults = case.data.shape
        #self.data[self.itime, self.itotal, :] = [fd, oxx, oyy,
        #                                         txy, angle,
        #                                         majorP, minorP, ovm]

        keys_map[key] = (case.subtitle, case.label,
                         case.superelement_adaptivity_index, case.pval_step)

        #nnodes_per_element = case.nnodes
        #nelements_nnodes = nnodes_nlayers // 2
        #nelements = nelements_nnodes // nnodes_per_element
        #nlayers = 2
        #nnodes = case.get_nnodes()
        scalars = case.data
        #print('scalars.shape', scalars.shape)
        #ntimes, nall, nresults = scalars.shape
        #nelements = nall // nnodes
        #scalars_save = scalars.reshape(ntimes, nelements, nnodes, nresults)
        #scalars_array.append(scalars_save[:, :, 0, :])
        print(scalars.shape)
        scalars_array.append(scalars)

    if len(scalars_array) == 0:
        return icase

    if len(scalars_array) == 1:
        scalars_array = scalars_array[0]
    else:
        scalars_array = np.concatenate(scalars_array, axis=1)
    print('***', scalars_array.shape, methods)

    #titles = []  # legend title
    headers = [] # sidebar word
    res = SimpleTableResults(
        subcase_id, headers, spring_ieids, ieid_max, scalars_array, methods,
        data_format=data_format,
        colormap='jet', uname='Spring ' + word)

    icase = _add_methods_to_form(icase, cases, key, subcase_id, word, res, case,
                                 form_dict, header_dict, methods,
                                 name='Spring')
    return icase

def _add_methods_to_form(icase, cases, key, subcase_id, word, res, case,
                         form_dict, header_dict,
                         methods, name):
    times = case._times
    nmethods = len(methods)
    if nmethods == 1:
        imethod = 0
        method = methods[imethod]
        for itime, dt in enumerate(times):
            #dt = case._times[itime]
            print('itime =', itime)
            header = _get_nastran_header(case, dt, itime)
            header_dict[(key, itime)] = header

            form = form_dict[(key, itime)]
            form.append((name + ' ' + word, icase, []))
            cases[icase] = (res, (subcase_id, (itime, imethod, header)))
            icase += 1
    else:
        for itime, dt in enumerate(times):
            #dt = case._times[itime]
            header = _get_nastran_header(case, dt, itime)
            header_dict[(key, itime)] = header

            formi = []
            form = form_dict[(key, itime)]
            form.append((name + ' ' + word, None, formi))
            # formi = form[0][2]

            for imethod, method in enumerate(methods):
                #cases[icase] = (res, (subcase_id, header))
                cases[icase] = (res, (subcase_id, (itime, imethod, header)))
                formi.append((method, icase, []))
                icase += 1
    return icase