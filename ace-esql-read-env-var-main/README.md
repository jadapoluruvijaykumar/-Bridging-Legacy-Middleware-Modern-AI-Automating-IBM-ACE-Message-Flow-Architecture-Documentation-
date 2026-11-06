# ace-esql-read-env-var
Example of reading environment variables from ESQL

# Calling Java from ESQL

## Code

Declaration in ESQL to enable access to the Java method:
```
CREATE FUNCTION javaLangSystemGetenv( IN name CHARACTER )
  RETURNS CHARACTER
  LANGUAGE JAVA
  EXTERNAL NAME "java.lang.System.getenv";
```

followed by using the Java function

```
    DECLARE pathValue CHARACTER;
    SET pathValue = javaLangSystemGetenv('PATH');
```        

## Example with ACE 11.0.0.18

```
tdolby@IBM-PF3K066L:~/workspace/WMB/src$ mqsicreateworkdir /tmp/ace-esql-read-env-var-work-dir
mqsicreateworkdir: Copying sample server.config.yaml to work directory
1 file(s) copied.
Successful command completion.
tdolby@IBM-PF3K066L:~/workspace/WMB/src$ mqsibar -c -w /tmp/ace-esql-read-env-var-work-dir -a /home/tdolby/git/ace-esql-read-env-var/ReadEnvVarApplication/ReadEnvVarApplication.bar
Generating runtime objects: '/tmp/ace-esql-read-env-var-work-dir/run' ...

BIP8071I: Successful command completion.
tdolby@IBM-PF3K066L:~/workspace/WMB/src$ IntegrationServer -w /tmp/ace-esql-read-env-var-work-dir --no-nodejs
.....2022-08-24 14:36:48.073280: .2022-08-24 09:36:48.073576: BIP1990I: Integration server 'ace-esql-read-env-var-work-dir' starting initialization; version '11.0.0.18' (64-bit)
.........................................2022-08-24 09:36:49.241360: BIP2155I: About to 'Initialize' the deployed resource 'ReadEnvVarApplication' of type 'Application'.
2022-08-24 09:36:49.362440: BIP2155I: About to 'Start' the deployed resource 'ReadEnvVarApplication' of type 'Application'.
2022-08-24 09:36:49.362632: BIP2269I: Deployed resource 'TimerFlow' (uuid='TimerFlow',type='MessageFlow') started successfully.
...
2022-08-24 09:36:49.362912: BIP1991I: Integration server has finished initialization.
2022-08-24 09:36:49.364256: BIP8099I: Read env var: PATH  -  /opt/ace-11.0.0.18/common/jdk/jre/bin:/var/mqsi/extensions/11.0.0/server/bin:/var/mqsi/extensions/11.0.0/bin:/opt/ace-11.0.0.18/server/bin:/opt/ace-11.0.0.18/common/node/bin:/opt/mqm/bin:/opt/ace-11.0.0.18/tools:/home/tdolby/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin
```

# Using server.conf.yaml (ACE 12.0.4 and later)

## server.conf.yaml

The server.conf.yaml UserVariables section can be used to read the environment
variable if resolveUserVariableEnvVars is set to true:

```
UserVariables:
  envVarUserVariable: '${PATH}'
resolveUserVariableEnvVars: true
```

which will load the contents of PATH into `envVarUserVariable` on startup.

## Code

Declaration in ESQL to enable access to the user variable:

```
DECLARE envVarUserVariable EXTERNAL CHARACTER;
```

at which point the ESQL variable `envVarUserVariable` can be used as normal.

## Example with ACE 12.0.4

```
tdolby@IBM-PF3K066L:/$ mqsicreateworkdir /tmp/ace-esql-read-env-var-work-dir
mqsicreateworkdir: Copying sample server.config.yaml to work directory
1 file(s) copied.
Successful command completion.
tdolby@IBM-PF3K066L:/$ mqsibar -c -w /tmp/ace-esql-read-env-var-work-dir -a /home/tdolby/git/ace-esql-read-env-var/UserVariableApplication/UserVariableApplication.bar
Generating runtime objects: '/tmp/ace-esql-read-env-var-work-dir/run' ...

BIP8071I: Successful command completion. 
tdolby@IBM-PF3K066L:/$ cp /home/tdolby/git/ace-esql-read-env-var/UserVariableApplication/server.conf.yaml /tmp/ace-esql-read-env-var-work-dir/overrides/server.conf.yaml
tdolby@IBM-PF3K066L:/$ IntegrationServer -w /tmp/ace-esql-read-env-var-work-dir
2022-12-08 15:21:49.476488: BIP1990I: Integration server 'ace-esql-read-env-var-work-dir' starting initialization; version '12.0.4.0' (64-bit) 
2022-12-08 15:21:49.479756: BIP9905I: Initializing resource managers. 
2022-12-08 15:21:51.474680: BIP9906I: Reading deployed resources. 
2022-12-08 15:21:51.476240: BIP9907I: Initializing deployed resources. 
2022-12-08 15:21:51.477384: BIP2155I: About to 'Initialize' the deployed resource 'UserVariableApplication' of type 'Application'. 
2022-12-08 15:21:51.601656: BIP2155I: About to 'Start' the deployed resource 'UserVariableApplication' of type 'Application'. 
2022-12-08 15:21:51.601860: BIP2269I: Deployed resource 'TimerFlow' (uuid='TimerFlow',type='MessageFlow') started successfully. 
2022-12-08 15:21:51.852376: BIP2866I: IBM App Connect Enterprise administration security is inactive. 
2022-12-08 15:21:51.859792: BIP3132I: The HTTP Listener has started listening on port '7600' for 'RestAdmin http' connections. 
2022-12-08 15:21:51.864480: BIP1991I: Integration server has finished initialization. 
2022-12-08 15:21:52.603864: BIP8099I: Read env var: from server.conf.yaml  -  /opt/ace-12.0.4.0/common/jdk/jre/bin:/var/mqsi/extensions/12.0.4/server/bin:/var/mqsi/extensions/12.0.4/bin:/opt/ace-12.0.4.0/server/bin:/opt/ace-12.0.4.0/common/node/bin:/opt/ace-12.0.4.0/tools:/home/tdolby/.local/bin:/home/tdolby/.local/bin:/home/tdolby/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin
```

Errors of the form

```
2022-12-08 15:23:22.016276: BIP9320E: Message Flow 'TimerFlow', 'TimerFlow' encountered a failure and could not start. 
2022-12-08 15:23:22.016344: BIP4001E: Syntax error in SQL statements in node 'TimerFlow.Compute'. 
2022-12-08 15:23:22.016374: BIP4055E: (.envVarUserVariable, 1.1) : User defined attribute must specify an initial value expression. 
```

mean that the user variable has not been set, which is usually an issue with server.conf.yaml in some way. The ESQL
declaration does not need an initial value, as the value should come from the environment variable.
