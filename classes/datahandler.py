# -*- coding: utf-8 -*-

import json
import pickle
import os
import sys
import copy
import numpy as np
import pandas as pd
from itertools import count
from teaser.project import Project
from classes.envelope import Envelope
from classes.solar import Sun
from classes.users import Users
from classes.system import BES
from classes.plots import DemandPlots
import functions.clustering_medoid as cm


class Datahandler:
    """
    Abstract class for data handling.
    Collects data from input files, TEASER, User and Envelope.

    Attributes
    ----------
    site:
        Dict for site data, e.g. weather.
    time:
        Dict for time settings.
    district:
        List of all buildings within district.
    scenario_name:
        Name of scenario file.
    scenario:
        Scenario data.
    counter:
        Dict for counting number of equal building types.
    srcPath:
        Source path.
    filePath:
        File path.
    """

    def __init__(self):
        """
        Constructor of Datahandler class.

        Returns
        -------
        None.
        """

        self.site = {}
        self.time = {}
        self.district = []
        self.scenario_name = None
        self.scenario = None
        self.counter = {}
        self.srcPath = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.filePath = os.path.join(self.srcPath, 'data')
        self.resultPath = os.path.join(self.srcPath, 'results', 'demands')

    def generateEnvironment(self):
        """
        Load physical district environment - site and weather.

        Returns
        -------
        None.
        """

        # %% load information about of the site under consideration
        # important for weather conditions
        with open(os.path.join(self.filePath, 'site_data.json')) as json_file:
            jsonData = json.load(json_file)
            for subData in jsonData:
                self.site[subData["name"]] = subData["value"]

        # %% load weather data for site
        # extract irradiation and ambient temperature
        if self.site["TRYYear"] == "TRY2015":
            first_row = 35
        elif self.site["TRYYear"] == "TRY2045":
            first_row = 37

        weatherData = np.loadtxt(os.path.join(self.filePath, 'weather')
                                 + "/"
                                 + self.site["TRYYear"] + "_Zone"
                                 + str(self.site["climateZone"]) + "_"
                                 + self.site["TRYType"] + ".txt",
                                 skiprows=first_row - 1)

        # weather data starts with 1st january at 1:00 am.
        # Add data point for 0:00 am to be able to perform interpolation.
        weatherData_temp = weatherData[-1:, :]
        weatherData = np.append(weatherData_temp, weatherData, axis=0)

        # get weather data of interest
        [temp_sunDirect, temp_sunDiff, temp_temp] = [weatherData[:, 12], weatherData[:, 13], weatherData[:, 5]]

        # %% load time information and requirements
        # needed for data conversion into the right time format
        with open(os.path.join(self.filePath, 'time_data.json')) as json_file:
            jsonData = json.load(json_file)
            for subData in jsonData :
                self.time[subData["name"]] = subData["value"]
        self.time["timeSteps"] = int(self.time["dataLength"] / self.time["timeResolution"])

        # interpolate input data to achieve required data resolution
        # transformation from values for points in time to values for time intervals
        self.site["SunDirect"] = np.interp(np.arange(0, self.time["dataLength"]+1, self.time["timeResolution"]),
                                           np.arange(0, self.time["dataLength"]+1, self.time["dataResolution"]),
                                           temp_sunDirect)[0:-1]
        self.site["SunDiffuse"] = np.interp(np.arange(0, self.time["dataLength"]+1, self.time["timeResolution"]),
                                            np.arange(0, self.time["dataLength"]+1, self.time["dataResolution"]),
                                            temp_sunDiff)[0:-1]
        self.site["T_e"] = np.interp(np.arange(0, self.time["dataLength"]+1, self.time["timeResolution"]),
                                     np.arange(0, self.time["dataLength"]+1, self.time["dataResolution"]),
                                     temp_temp)[0:-1]

        self.site["SunTotal"] = self.site["SunDirect"] + self.site["SunDiffuse"]

        # Calculate solar irradiance per surface direction - S, W, N, E, Roof represented by angles gamma and beta
        global sun
        sun = Sun(filePath=self.filePath)
        self.SunRad = sun.getSolarGains(initialTime=0,
                                        timeDiscretization=self.time["timeResolution"],
                                        timeSteps=self.time["timeSteps"],
                                        timeZone=self.site["timeZone"],
                                        location=self.site["location"],
                                        altitude=self.site["altitude"],
                                        beta=[90, 90, 90, 90, 0],
                                        gamma=[0, 90, 180, 270, 0],
                                        beamRadiation=self.site["SunDirect"],
                                        diffuseRadiation=self.site["SunDiffuse"],
                                        albedo=self.site["albedo"])

    def initializeBuildings(self, scenario_name='example'):
        """
        Fill district with buildings from scenario file.

        Parameters
        ----------
        scenario_name: string, optional
            Name of scenario file to be read. The default is 'example'.

        Returns
        -------
        None.
        """

        self.scenario_name = scenario_name
        self.scenario = pd.read_csv(os.path.join(self.filePath, 'scenarios')
                                    + "/"
                                    + self.scenario_name + ".csv",
                                    header=0, delimiter=";")

        # initialize buildings for scenario
        # loop over all buildings
        for id in self.scenario["id"]:

            # create empty dict for observed building
            building = {}

            # store features of the observed building
            building["buildingFeatures"] = self.scenario.loc[id]

            # append building to district
            self.district.append(building)

    def generateBuildings(self):
        """
        Load building envelope and user data.

        Returns
        -------
        None.
        """

        # %% load general building information
        # contains definitions and parameters that affect all buildings
        bldgs = {}
        with open(os.path.join(self.filePath, 'design_building_data.json')) as json_file:
            jsonData = json.load(json_file)
            for subData in jsonData:
                bldgs[subData["name"]] = subData["value"]

        # %% create TEASER project
        # create one project for the whole district
        prj = Project(load_data=True)
        prj.name = self.scenario_name

        for building in self.district:

            # convert short names into designation needed for TEASER
            building_type = bldgs["buildings_long"][bldgs["buildings_short"].index(building["buildingFeatures"]["building"])]
            retrofit_level = bldgs["retrofit_long"][bldgs["retrofit_short"].index(building["buildingFeatures"]["retrofit"])]

            # add buildings to TEASER project
            prj.add_residential(method='tabula_de',
                                usage=building_type,
                                name="ResidentialBuildingTabula",
                                year_of_construction=building["buildingFeatures"]["year"],
                                number_of_floors=3,
                                height_of_floors=3.125,
                                net_leased_area=building["buildingFeatures"]["area"],
                                construction_type=retrofit_level)

            # %% create envelope object
            # containing all physical data of the envelope
            building["envelope"] = Envelope(prj=prj,
                                            building_params=building["buildingFeatures"],
                                            construction_type=retrofit_level,
                                            file_path=self.filePath)

            # %% create user object
            # containing number occupants, electricity demand,...
            building["user"] = Users(building=building["buildingFeatures"]["building"],
                                     area=building["buildingFeatures"]["area"])

    def generateDemands(self, calcUserProfiles=True, saveUserProfiles=True):
        """
        Generate occupancy profile, heat demand, domestic hot water demand and heating demand.

        Parameters
        ----------
        calcUserProfiles: bool, optional
            True: calculate new user profiles.
            False: load user profiles from file.
            The default is True.
        saveUserProfiles: bool, optional
            True for saving calculated user profiles in workspace (Only taken into account if calcUserProfile is True).
            The default is True.

        Returns
        -------
        None.
        """

        set = []
        for building in self.district:

            # %% create unique building name
            # needed for loading and storing data with unique name
            # name is composed of building type, number of flats, serial number of building of this properties
            name = building["buildingFeatures"]["building"] + "_" + str(building["user"].nb_flats)
            if name not in set:
                set.append(name)
                self.counter[name] = count()
            nb = next(self.counter[name])
            building["unique_name"] = name + "_" + str(nb)

            # calculate or load user profiles
            if calcUserProfiles:
                building["user"].calcProfiles(site=self.site,
                                              time_resolution=self.time["timeResolution"],
                                              time_horizon=self.time["dataLength"])

                if saveUserProfiles:
                    building["user"].saveProfiles(building["unique_name"], self.resultPath)

                print("Calculate demands of building " + building["unique_name"])

            else:
                building["user"].loadProfiles(building["unique_name"], self.resultPath)
                print("Load demands of building " + building["unique_name"])

            # check if EV exist
            building["clusteringData"] = {
                "potentialEV": copy.deepcopy(building["user"].car)
            }
            building["user"].car *= building["buildingFeatures"]["EV"]

            building["envelope"].calcNormativeProperties(self.SunRad, building["user"].gains)

            # calculate heating profiles
            building["user"].calcHeatingProfile(site=self.site,
                                                envelope=building["envelope"],
                                                time_resolution=self.time["timeResolution"])

            if saveUserProfiles:
                building["user"].saveHeatingProfile(building["unique_name"], self.resultPath)

        print("Finished generating demands!")

    def generateDistrictComplete(self, scenario_name='example', calcUserProfiles=True, saveUserProfiles=True,
                                 designDevs=False, saveGenProfiles=True, clustering=False):
        """
        All in one solution for district and demand generation.

        Parameters
        ----------
        scenario_name:string, optional
            Name of scenario file to be read. The default is 'example'.
        calcUserProfiles: bool, optional
            True: calculate new user profiles.
            False: load user profiles from file.
            The default is True.
        saveUserProfiles: bool, optional
            True for saving calculated user profiles in workspace (Only taken into account if calcUserProfile is True).
            The default is True.
        designDevs: bool, optional
            Decision if devices will be designed. The default is False.
        saveGenProfiles: bool, optional
            Decision if generation profiles of designed devices will be saved. Just relevant if 'designDevs=True'.
            The default is True.
        clustering: bool, optional
            Decision if profiles will be clustered. The default is False.

        Returns
        -------
        None.
        """

        self.generateEnvironment()
        self.initializeBuildings(scenario_name)
        self.generateBuildings()
        self.generateDemands(calcUserProfiles, saveUserProfiles)
        if designDevs:
            self.designDevices(saveGenerationProfiles=saveGenProfiles)
        if clustering:
            self.clusterProfiles()

    def designDevices(self, saveGenerationProfiles=True):
        """
        Calculate device capacities, calculate PV and STC generation profiles as well as EV load profiles.

        Parameters
        ----------
        saveGenerationProfiles : bool, optional
            True: save PV and STC profiles.
            False: don't save PV and STC profiles.
            The default is True.

        Returns
        -------
        None.
        """

        for building in self.district:

            # %% load general building information
            # contains definitions and parameters that affect all buildings
            bldgs = {}
            with open(os.path.join(self.filePath, 'design_building_data.json')) as json_file:
                jsonData = json.load(json_file)
                for subData in jsonData:
                    bldgs[subData["name"]] = subData["value"]

            # %% calculate design heat loads
            # at norm outside temperature
            building["heatload"] = building["envelope"].calcHeatLoad(site=self.site, method="design")
            # at bivalent temperature
            building["bivalent"] = building["envelope"].calcHeatLoad(site=self.site, method="bivalent")
            # at heating limit temperature
            building["heatlimit"] = building["envelope"].calcHeatLoad(site=self.site, method="heatlimit")
            # for drinking hot water
            building["dhwload"] = \
                bldgs["dhwload"][bldgs["buildings_short"].index(building["buildingFeatures"]["building"])] * \
                building["user"].nb_flats

            # %% create building energy system object
            # get capacities of all possible devices
            bes_obj = BES(file_path=self.filePath)
            building["capacities"] = bes_obj.designECS(building, self.site)

            # calculate theoretical PV generation
            potentialPV, potentialSTC = \
                sun.calcPVAndSTCProfile(time=self.time,
                                        site=self.site,
                                        area_roof=building["envelope"].A["opaque"]["roof"],
                                        beta=[35],
                                        gamma=[building["buildingFeatures"]["gamma_PV"]],
                                        usageFactorPV=building["buildingFeatures"]["f_PV"],
                                        usageFactorSTC=building["buildingFeatures"]["f_STC"])

            # assign real PV generation to building
            building["generationPV"] = potentialPV * building["buildingFeatures"]["PV"]

            # assign real STC generation to building
            building["generationSTC"] = potentialSTC * building["buildingFeatures"]["STC"]

            # clustering data
            building["clusteringData"]["potentialPV"] = potentialPV
            building["clusteringData"]["potentialSTC"] = potentialSTC

            # optionally save generation profiles
            if saveGenerationProfiles == True:
                np.savetxt(self.resultPath + '/generationPV_' + building["unique_name"] + '.csv',
                           building["generationPV"],
                           delimiter=',')
                np.savetxt(self.resultPath + '/generationSTC_' + building["unique_name"] + '.csv',
                           building["generationSTC"],
                           delimiter=',')

    def clusterProfiles(self):
        """
        Perform time series aggregation for profiles by using the k-medoids clustering algorithm.

        Returns
        -------
        None.
        """

        # calculate length of array
        initialArrayLenght = (self.time["clusterLength"] / self.time["timeResolution"])
        lenghtArray = initialArrayLenght
        while lenghtArray <= len(self.site["T_e"]):
            lenghtArray += initialArrayLenght
        lenghtArray -= initialArrayLenght
        lenghtArray = int(lenghtArray)

        # adjust profiles with calculated array length
        adjProfiles = {}
        for id in self.scenario["id"]:
            adjProfiles[id] = {}
            adjProfiles[id]["elec"] = self.district[id]["user"].elec[0:lenghtArray]
            adjProfiles[id]["dhw"] = self.district[id]["user"].dhw[0:lenghtArray]
            adjProfiles[id]["gains"] = self.district[id]["user"].gains[0:lenghtArray]
            adjProfiles[id]["occ"] = self.district[id]["user"].occ[0:lenghtArray]
            adjProfiles[id]["heat"] = self.district[id]["user"].heat[0:lenghtArray]
            if self.district[id]["buildingFeatures"]["EV"] != 0:
                adjProfiles[id]["car"] = self.district[id]["user"].car[0:lenghtArray]
            else:
                # no EV exists; but array with just zeros leads to problem while clustering
                adjProfiles[id]["car"] = self.district[id]["clusteringData"]["potentialEV"][0:lenghtArray] * sys.float_info.epsilon  # np.ones(lenghtArray) * sys.float_info.epsilon * 100
            if self.district[id]["buildingFeatures"]["PV"] != 0:
                adjProfiles[id]["generationPV"] = self.district[id]["generationPV"][0:lenghtArray]
            else:
                # no PV module installed; but array with just zeros leads to problem while clustering
                adjProfiles[id]["generationPV"] = self.district[id]["clusteringData"]["potentialPV"][0:lenghtArray] * sys.float_info.epsilon  # np.ones(lenghtArray) * sys.float_info.epsilon * 100
            if self.district[id]["buildingFeatures"]["STC"] != 0:
                adjProfiles[id]["generationSTC"] = self.district[id]["generationSTC"][0:lenghtArray]
            else:
                # no STC installed; but array with just zeros leads to problem while clustering
                adjProfiles[id]["generationSTC"] = self.district[id]["clusteringData"]["potentialSTC"][0:lenghtArray] * sys.float_info.epsilon  # np.ones(lenghtArray) * sys.float_info.epsilon * 100

        adjProfiles["Sun"] = {}
        for drct in range(len(self.SunRad)):
            adjProfiles["Sun"][drct] = self.SunRad[drct][0:lenghtArray]

        adjProfiles["T_e"] = self.site["T_e"][0:lenghtArray]

        # Prepare clustering
        inputsClustering = []

        for id in self.scenario["id"]:
            inputsClustering.append(adjProfiles[id]["elec"])
            inputsClustering.append(adjProfiles[id]["dhw"])
            inputsClustering.append(adjProfiles[id]["gains"])
            inputsClustering.append(adjProfiles[id]["occ"])
            inputsClustering.append(adjProfiles[id]["car"])
            inputsClustering.append(adjProfiles[id]["generationPV"])
            inputsClustering.append(adjProfiles[id]["generationSTC"])
            inputsClustering.append(adjProfiles[id]["heat"])

        for drct in range(len(self.SunRad)):
            inputsClustering.append(adjProfiles["Sun"][drct])

        inputsClustering.append(adjProfiles["T_e"])

        # weights for clustering algorithm indicating the focus onto this profile
        weights = np.ones(len(inputsClustering))
        # higher weight for outdoor temperature (should at least have the same weight as number of buildings)
        weights[-1] = len(self.scenario["id"]) + len(self.SunRad)

        # Perform clustering
        (newProfiles, nc, y, z, transfProfiles) = cm.cluster(np.array(inputsClustering),
                                                             number_clusters=self.time["clusterNumber"],
                                                             len_day=int(initialArrayLenght),
                                                             weights=weights)

        # safe clustering solution in district data
        for id in self.scenario["id"]:
            index_house = int(8)
            self.district[id]["user"].elec_cluster = newProfiles[index_house * id]
            self.district[id]["user"].dhw_cluster = newProfiles[index_house * id + 1]
            self.district[id]["user"].gains_cluster = newProfiles[index_house * id + 2]
            self.district[id]["user"].occ_cluster = newProfiles[index_house * id + 3]
            # assign real EV, PV and STC generation for clustered data to buildings
            # (array with zeroes if EV, PV or STC does not exist)
            self.district[id]["user"].car_cluster = newProfiles[index_house * id + 4] * self.scenario.loc[id]["EV"]
            self.district[id]["generationPV_cluster"] = newProfiles[index_house * id + 5] \
                                                        * self.district[id]["buildingFeatures"]["PV"]
            self.district[id]["generationSTC_cluster"] = newProfiles[index_house * id + 6] \
                                                         * self.district[id]["buildingFeatures"]["STC"]
            self.district[id]["user"].heat_cluster = newProfiles[index_house * id + 7]

        self.SunRad_cluster = {}
        for drct in range(len(self.SunRad)):
            self.SunRad_cluster[drct] = newProfiles[-1 - len(self.SunRad) + drct]

        self.site["T_e_cluster"] = newProfiles[-1]

        # clusters
        self.clusters = []
        for i in range(len(y)):
            if y[i] != 0:
                self.clusters.append(i)

        # clusters and their assigned nodes (days/weeks/etc)
        self.clusterAssignments = {}
        for c in self.clusters:
            self.clusterAssignments[c] = []
            temp = z[c]
            for i in range(len(temp)):
                if temp[i] == 1:
                    self.clusterAssignments[c].append(i)

        # weights indicating how often a cluster appears
        self.clusterWeights = {}
        for c in self.clusters:
            self.clusterWeights[c] = len(self.clusterAssignments[c])

        """self.clusteringCheck = {}
        for c in self.clusters:
            self.clusteringCheck[c] = sum(self.clusterAssignments[c])"""

    def saveDistrict(self):
        """
        Save district dict as pickle file.

        Returns
        -------
        None.
        """

        with open(self.resultPath + "/" + self.scenario_name + ".p", 'wb') as fp:
            pickle.dump(self.district, fp, protocol=pickle.HIGHEST_PROTOCOL)

    def loadDistrict(self, scenario_name='example'):
        """
        Load district dict from pickle file.

        Parameters
        ----------
        scenario_name : string, optional
            Name of district file to be read. The default is 'example'.

        Returns
        -------
        None.
        """

        self.scenario_name = scenario_name

        with open(self.resultPath + "/" + self.scenario_name + ".p", 'rb') as fp:
            self.district = pickle.load(fp)

    def plot(self, mode='default', initialTime=0, timeHorizon=31536000, savePlots=True, timeStamp=False, show=False):
        """
        Create plots of the energy consumption and generation.

        Parameters
        ----------
        mode : string, optional
            Choose a single plot or show all of them as default. The default is 'default'.
            Possible modes are:
            ['elec', 'dhw', 'gains', 'occ', 'car', 'heating', 'pv', 'stc', 'electricityDemand', 'heatDemand'].
        initialTime : integer, optional
            Start of the plot in seconds from the beginning of the year. The default is 0.
        timeHorizon : integer, optional
            Length of the time horizon that is plotted in seconds. The default is 31536000 (what equals one year).
        savePlots : boolean, optional
            Decision if plots are saved under results/plots/. The default is True.
        timeStamp : boolean, optional
            Decision if saved plots get a unique name by adding a time stamp. The default is False.
        show : boolean, optional
            Decision if saved plots are presented directly to the user. The default is False.

        Returns
        -------
        None.
        """

        # initialize plots and prepare data for plotting
        demandPlots = DemandPlots()
        demandPlots.preparePlots(self)

        # check which resolution for plots is used
        if initialTime == 0 and timeHorizon == 31536000:
            plotResolution = 'monthly'
        else:
            plotResolution = 'stepwise'

        # the selection of possible plots
        plotTypes = ['elec', 'dhw', 'gains', 'occ', 'car', 'heating', 'pv', 'stc', 'electricityDemand', 'heatDemand']

        if mode == 'default':
            # create all default plots
            demandPlots.defaultPlots(plotResolution, initialTime=initialTime, timeHorizon=timeHorizon,
                                     savePlots=savePlots, timeStamp=timeStamp, show=show)
        elif mode in plotTypes:
            # create a plot
            demandPlots.onePlot(plotType=mode, plotResolution=plotResolution, initialTime=initialTime,
                                timeHorizon=timeHorizon, savePlots=savePlots, timeStamp=timeStamp, show=show)
        else:
            # print massage that input is not valid
            print('\n Selected plot mode is not valid. So no plot could de generated. \n')