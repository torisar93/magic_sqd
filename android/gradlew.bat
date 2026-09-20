@rem Минимальный gradlew.bat (2026-09-20): см. gradlew — тот же вызов GradleWrapperMain из gradle-wrapper.jar.
@echo off
setlocal
set APP_HOME=%~dp0
if defined JAVA_HOME (set JAVACMD=%JAVA_HOME%\bin\java.exe) else (set JAVACMD=java.exe)
"%JAVACMD%" -Xmx64m -Xms64m %JAVA_OPTS% %GRADLE_OPTS% "-Dorg.gradle.appname=gradlew" -classpath "%APP_HOME%gradle\wrapper\gradle-wrapper.jar" org.gradle.wrapper.GradleWrapperMain %*
endlocal
